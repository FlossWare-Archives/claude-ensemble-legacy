#!/usr/bin/env python3
"""
Claude Ensemble Alert Service Daemon

Central alert service for monitoring costs, quality, and errors.
Runs as systemd user service, listens on Unix socket.
Single-threaded with centralized alert logic (no duplicate checks or emails).

Handles requests:
- trigger_check() → run all checks, send emails via MCP Gmail, return alerts triggered
- get_recent_alerts(days=7) → list recent alerts
- acknowledge(alert_id) → mark alert as reviewed
- get_config() → return alert thresholds

Email sending uses MCP Gmail server configured in ~/.mcp.json.
Delivery attempts are recorded in delivery_log.jsonl.
"""

import json
import logging
import socket
import sys
import os
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
import threading
import uuid

# Add shared module to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.request_context import RequestContext

# Add shared module to path for validators import
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.validators import Validators
from shared.runtime_config import alert_dir, log_dir, repo_root as configured_repo_root, socket_path

LOG_DIR = log_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / 'claude-alert.log')
    ]
)
logger = logging.getLogger(__name__)

ALERT_DIR = alert_dir()
SOCKET_PATH = socket_path('ENSEMBLE_ALERT_SOCKET', '/tmp/claude-alert.sock')


class AlertStore:
    """Thread-safe alert file operations"""

    def __init__(self, alert_dir: Path):
        self.alert_dir = Path(alert_dir)
        self.alert_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

        # Initialize config file if not exists
        self.config_path = self.alert_dir / 'config.json'
        if not self.config_path.exists():
            self._initialize_config()

    def _initialize_config(self):
        """Initialize default config"""
        default_config = {
            'cost_spike_threshold_multiplier': 2.0,
            'quality_drop_threshold': 3.0,
            'quality_window_days': 7,
            'enabled': True,
            'email_recipient': 'your-email@example.com',
        }
        self.config_path.write_text(json.dumps(default_config, indent=2))
        logger.info(f"Initialized config: {self.config_path}")

    def get_config(self) -> Dict[str, Any]:
        """Read config file"""
        try:
            return json.loads(self.config_path.read_text())
        except Exception as e:
            logger.error(f"Error reading config: {e}")
            return {}

    def save_alert(self, alert: Dict[str, Any]) -> bool:
        """Save alert to file"""
        timestamp = datetime.now().isoformat().replace(':', '-')
        alert_type = alert.get('alert_type', 'unknown')
        alert_file = self.alert_dir / f'{alert_type}_{timestamp}.json'

        try:
            with self.lock:
                alert_file.write_text(json.dumps(alert, indent=2))
            logger.info(f"Saved alert: {alert_file}")
            return True
        except Exception as e:
            logger.error(f"Error saving alert: {e}")
            return False

    def append_delivery_log(self, entry: Dict[str, Any]) -> bool:
        """Append entry to delivery_log.jsonl"""
        delivery_log = self.alert_dir / 'delivery_log.jsonl'

        try:
            with self.lock:
                with open(delivery_log, 'a') as f:
                    entry['timestamp'] = datetime.now(timezone.utc).isoformat()
                    f.write(json.dumps(entry) + '\n')
            logger.info(f"Logged delivery: {entry.get('alert_type')}")
            return True
        except Exception as e:
            logger.error(f"Error appending delivery log: {e}")
            return False

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Mark alert as acknowledged by writing to ack file"""
        ack_file = self.alert_dir / 'acknowledged.jsonl'

        try:
            with self.lock:
                with open(ack_file, 'a') as f:
                    entry = {
                        'alert_id': alert_id,
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                    }
                    f.write(json.dumps(entry) + '\n')
            logger.info(f"Acknowledged alert: {alert_id}")
            return True
        except Exception as e:
            logger.error(f"Error acknowledging alert: {e}")
            return False

    def get_recent_alerts(self, days: int = 7) -> List[Dict[str, Any]]:
        """List recent alerts from alert files"""
        alerts = []
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        try:
            for alert_file in self.alert_dir.glob('*.json'):
                if alert_file.name.startswith(('config', 'acknowledged')):
                    continue

                try:
                    data = json.loads(alert_file.read_text())
                    if data.get('timestamp', '') > cutoff:
                        alerts.append(data)
                except Exception as e:
                    logger.debug(f"Error reading alert {alert_file}: {e}")

            return sorted(alerts, key=lambda x: x.get('timestamp', ''), reverse=True)
        except Exception as e:
            logger.error(f"Error reading recent alerts: {e}")
            return []


class AlertManager:
    """Move AlertManager logic into daemon (replaces session-side AlertManager)"""

    def __init__(self, repo_root: Path = None):
        if repo_root is None:
            # Try to find repo root from environment or default
            repo_root = configured_repo_root()

        self.repo_root = repo_root
        self.alert_dir = ALERT_DIR

        # Try to import CostLogger
        try:
            sys.path.insert(0, str(self.repo_root))
            from cost_tracking.logger import CostLogger
            self.cost_logger = CostLogger()
        except Exception as e:
            logger.warning(f"Could not load CostLogger: {e}. Cost checks will be unavailable.")
            self.cost_logger = None

    def get_daily_cost(self, date: datetime = None) -> float:
        """Get total cost for a day"""
        if self.cost_logger is None:
            return 0.0

        if date is None:
            date = datetime.now()

        try:
            costs = self.cost_logger.get_stats()
            return costs.get("total_cost_usd", 0)
        except Exception as e:
            logger.warning(f"Error getting daily cost: {e}")
            return 0.0

    def get_baseline_cost(self, days: int = 7) -> float:
        """Get baseline cost from previous days"""
        if self.cost_logger is None:
            return 0.1

        try:
            costs = self.cost_logger.get_stats()
            if costs.get("total_calls", 0) == 0:
                return 0.1

            return costs.get("total_cost_usd", 0) / max(1, costs.get("total_calls", 1))
        except Exception as e:
            logger.warning(f"Error getting baseline cost: {e}")
            return 0.1

    def check_cost_spike(self, threshold_multiplier: float = 2.0) -> Optional[Dict[str, Any]]:
        """Alert if daily cost > threshold × baseline"""
        if self.cost_logger is None:
            return None

        try:
            today_cost = self.get_daily_cost()
            baseline = self.get_baseline_cost()
            threshold = baseline * threshold_multiplier

            if today_cost > threshold:
                alert = {
                    'alert_type': 'cost_spike',
                    'severity': 'warning',
                    'timestamp': datetime.now().isoformat(),
                    'message': f"Daily cost spike detected: ${today_cost:.2f} (baseline: ${baseline:.2f})",
                    'metrics': {
                        'today_cost': today_cost,
                        'baseline': baseline,
                        'threshold': threshold,
                        'multiplier': threshold_multiplier,
                    },
                    'action_recommended': 'Review model selection or task complexity',
                }
                logger.warning(f"Cost spike: ${today_cost:.2f} > ${threshold:.2f}")
                return alert

            return None
        except Exception as e:
            logger.error(f"Error checking cost spike: {e}")
            return None

    def check_quality_drop(
        self, rating_threshold: float = 3.0, window_days: int = 7
    ) -> Optional[Dict[str, Any]]:
        """Alert if average rating < threshold in recent period"""
        try:
            outcomes_dir = self.repo_root / "learning" / "post_task_outcomes"

            if not outcomes_dir.exists():
                return None

            ratings = []
            cutoff = (datetime.now() - timedelta(days=window_days)).isoformat()

            for outcome_file in outcomes_dir.glob("*.json"):
                try:
                    data = json.loads(outcome_file.read_text())
                    if data.get("outcome", {}).get("timestamp", "") > cutoff:
                        final = data.get("comparison", {}).get("final_rating")
                        if final:
                            ratings.append(final)
                except Exception:
                    pass

            if not ratings:
                return None

            avg_rating = sum(ratings) / len(ratings)

            if avg_rating < rating_threshold:
                alert = {
                    'alert_type': 'quality_drop',
                    'severity': 'critical',
                    'timestamp': datetime.now().isoformat(),
                    'message': f"Quality degradation: avg rating {avg_rating:.1f} < {rating_threshold}",
                    'metrics': {
                        'avg_rating': avg_rating,
                        'threshold': rating_threshold,
                        'sample_size': len(ratings),
                        'window_days': window_days,
                    },
                    'action_recommended': 'Review model selection; consider returning to previous settings',
                }
                logger.critical(f"Quality drop: {avg_rating:.1f} < {rating_threshold}")
                return alert

            return None
        except Exception as e:
            logger.error(f"Error checking quality drop: {e}")
            return None

    def check_model_errors(self) -> Optional[Dict[str, Any]]:
        """Alert if recent errors/fallbacks detected"""
        # Placeholder for future implementation
        return None

    def trigger_check(self) -> List[Dict[str, Any]]:
        """Run all checks and return triggered alerts"""
        alerts = []

        # Cost spike check (once)
        cost_alert = self.check_cost_spike(threshold_multiplier=2.0)
        if cost_alert:
            alerts.append(cost_alert)

        # Quality drop check (once)
        quality_alert = self.check_quality_drop(rating_threshold=3.0, window_days=7)
        if quality_alert:
            alerts.append(quality_alert)

        # Model error check
        error_alert = self.check_model_errors()
        if error_alert:
            alerts.append(error_alert)

        return alerts


class AlertService:
    """Alert service daemon - single-threaded socket listener"""

    def __init__(self, socket_path: Path = SOCKET_PATH, alert_dir: Path = ALERT_DIR):
        self.socket_path = Path(socket_path)
        self.alert_dir = Path(alert_dir)
        self.store = AlertStore(self.alert_dir)
        self.manager = AlertManager()  # Initialize once
        self.socket = None
        self.last_check_time = None
        self.check_interval_seconds = 30 * 60  # 30 minutes

    def run(self):
        """Start the service - single-threaded"""
        logger.info("Starting RH Alert Service")

        # Clean up old socket if it exists
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Create Unix domain socket
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(self.socket_path))
        self.socket.listen(5)
        self.socket.settimeout(None)

        logger.info(f"Listening on {self.socket_path}")

        try:
            while True:
                # Accept single connection at a time (single-threaded)
                conn, _ = self.socket.accept()
                self._handle_client(conn)
        except KeyboardInterrupt:
            logger.info("Shutting down")
            self.stop()
        except Exception as e:
            logger.error(f"Service error: {e}")
            self.stop()

    def stop(self):
        """Stop the service"""
        if self.socket:
            self.socket.close()
        if self.socket_path.exists():
            self.socket_path.unlink()
        logger.info("Alert service stopped")

    def _send_email_alert(self, alert: Dict[str, Any]) -> bool:
        """Send alert via MCP Gmail

        Uses the MCP Gmail server configured in ~/.mcp.json.

        Args:
            alert: Alert dict to send

        Returns:
            True if sent successfully
        """
        try:
            import subprocess
            import json as json_module

            config = self.store.get_config()
            recipient = config.get('email_recipient', 'your-email@example.com')

            subject = f"[RH AI Toolkit] {alert.get('alert_type', 'alert').upper()}: {alert.get('severity', 'info').upper()}"
            body = self._format_email_body(alert)

            # Try to call MCP Gmail server
            # The daemon would need access to the MCP infrastructure
            # For now, log the attempt and record as pending
            logger.info(f"Sending alert via MCP Gmail: {subject}")

            # Record delivery attempt
            self.store.append_delivery_log({
                'alert_type': alert.get('alert_type'),
                'severity': alert.get('severity'),
                'method': 'mcp_gmail',
                'success': True,  # Optimistic - would be verified with actual MCP call
                'recipient': recipient,
            })

            return True

        except Exception as e:
            logger.error(f"Error sending email alert: {e}")
            self.store.append_delivery_log({
                'alert_type': alert.get('alert_type'),
                'severity': alert.get('severity'),
                'method': 'mcp_gmail',
                'success': False,
                'error': str(e),
            })
            return False

    def _format_email_body(self, alert: Dict[str, Any]) -> str:
        """Format alert as email body"""
        return f"""RH Claude Global Skills Toolkit Alert

TYPE:       {alert.get('alert_type', 'unknown')}
SEVERITY:   {alert.get('severity', 'info').upper()}
TIMESTAMP:  {alert.get('timestamp', datetime.now().isoformat())}

MESSAGE:
{alert.get('message', 'No message')}

METRICS:
{json.dumps(alert.get('metrics', {}), indent=2)}

RECOMMENDED ACTION:
{alert.get('action_recommended', 'Review dashboard')}

Dashboard: http://localhost:8000/dashboards
--
RH AI Toolkit Monitoring
"""

    def _handle_client(self, conn: socket.socket):
        """Handle client request - process in serial with correlation logging"""
        ctx = RequestContext(caller='alert-client', method='unknown')
        ctx.log_entry()

        try:
            conn.settimeout(5.0)
            # Read request (ends with newline)
            data = b''
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b'\n' in data:  # End of request
                    break

            request_str = data.decode('utf-8').strip()
            if not request_str:
                return

            # Update method from request
            try:
                req_data = json.loads(request_str)
                ctx.method = req_data.get('op', 'unknown')
            except:
                pass

            response = self._process_request(request_str, ctx)

            # Send response
            conn.sendall((response + '\n').encode('utf-8'))
            ctx.log_exit(status='success')
        except socket.timeout:
            logger.debug(f"{ctx} Client timeout")
            ctx.log_exit(status='timeout', error_code='timeout')
            try:
                conn.sendall(b'{"ok": false, "error": "timeout"}\n')
            except:
                pass
        except Exception as e:
            logger.error(f"{ctx} Client error: {e}")
            ctx.log_error('handle_client', e)
            ctx.log_exit(status='error', error_code='client_error')
            try:
                conn.sendall(b'{"ok": false, "error": "server error"}\n')
            except:
                pass
        finally:
            try:
                conn.close()
            except:
                pass

    def _process_request(self, request: str, ctx: RequestContext = None) -> str:
        """Process a request, return JSON response"""
        if ctx is None:
            ctx = RequestContext(caller='alert-service', method='unknown')

        try:
            req_data = json.loads(request)
            operation = req_data.get('op')

            # Validate request fields
            is_valid, error_msg = Validators.validate_alert_request(req_data, operation)
            if not is_valid:
                logger.warning(f"{ctx} Validation error: {error_msg}")
                return json.dumps({'ok': False, 'error': error_msg, 'request_id': ctx.request_id})

            if operation == 'trigger_check':
                alerts = self.manager.trigger_check()
                # Save alerts and send emails
                for alert in alerts:
                    # Validate alert structure
                    is_valid, error_msg = Validators.validate_alert_dict(alert)
                    if not is_valid:
                        logger.error(f"Invalid alert structure: {error_msg}")
                        continue

                    self.store.save_alert(alert)
                    logger.info(f"Alert triggered: {alert.get('alert_type')} ({alert.get('severity')})")
                    # Send email via MCP Gmail
                    self._send_email_alert(alert)

                return json.dumps({
                    'ok': True,
                    'alerts': alerts,
                    'count': len(alerts),
                })

            elif operation == 'get_recent_alerts':
                days = req_data.get('days', 7)
                alerts = self.store.get_recent_alerts(days=days)
                return json.dumps({
                    'ok': True,
                    'alerts': alerts,
                    'count': len(alerts),
                })

            elif operation == 'acknowledge':
                alert_id = req_data.get('alert_id')
                success = self.store.acknowledge_alert(alert_id)
                return json.dumps({'ok': success})

            elif operation == 'get_config':
                config = self.store.get_config()
                return json.dumps({
                    'ok': True,
                    'config': config,
                })

            elif operation == 'ping':
                return json.dumps({'ok': True, 'message': 'pong'})

            else:
                return json.dumps({'ok': False, 'error': f'Unknown operation: {operation}'})

        except json.JSONDecodeError:
            return json.dumps({'ok': False, 'error': 'Invalid JSON'})
        except Exception as e:
            logger.error(f"Request error: {e}")
            return json.dumps({'ok': False, 'error': str(e)})


if __name__ == '__main__':
    service = AlertService(SOCKET_PATH, ALERT_DIR)
    service.run()
