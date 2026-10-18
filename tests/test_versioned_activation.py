#!/usr/bin/env python3
"""Isolated activation, rollback, and interrupted-recovery integration tests."""
import http.server, json, os, shutil, socketserver, subprocess, tempfile, threading, unittest
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
DEPLOYMENT_SOURCE=REPO/"tools"/"ce-deployment"
UNITS=("claude-memory.service","claude-thompson.service","claude-learning.service","claude-alert.service","claude-messenger.service","claude-graph.service","claude-ensemble.service")
class ReadyHandler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(b'{"ready":true}')
 def log_message(self,*args): pass
class ActivationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix="ce-activation-test-"); self.addCleanup(self.temp.cleanup)
  self.base=Path(self.temp.name); self.home=self.base/"home"; self.home.mkdir()
  self.root=self.home/".FlossWare"/"claude-ensemble"; self.root.mkdir(parents=True)
  self.units_dir=self.base/"units"; self.units_dir.mkdir()
  self.state=self.base/"active.txt"; self.log=self.base/"systemctl.log"
  self.bin=self.base/"bin"; self.bin.mkdir(); self.systemctl=self.bin/"systemctl"
  self.systemctl.write_text("""#!/usr/bin/env bash
set -eu
echo "$*" >> "$SYSTEMCTL_LOG"
[[ "${1:-}" == "--user" ]] && shift
action="${1:-}"; shift || true
case "$action" in
 stop) unit="$1"; grep -Fxv "$unit" "$SYSTEMCTL_STATE" > "$SYSTEMCTL_STATE.tmp" || true; mv "$SYSTEMCTL_STATE.tmp" "$SYSTEMCTL_STATE" ;;
 start) unit="$1"; current="$(readlink -f "$DEPLOY_ROOT/current" 2>/dev/null || true)"; if [[ "${FAIL_VERSION:-}" != "" && "$current" == "$DEPLOY_ROOT/releases/$FAIL_VERSION" && "$unit" == "claude-ensemble.service" ]]; then exit 1; fi; grep -Fxq "$unit" "$SYSTEMCTL_STATE" 2>/dev/null || echo "$unit" >> "$SYSTEMCTL_STATE" ;;
 is-active) unit="$2"; grep -Fxq "$unit" "$SYSTEMCTL_STATE" ;;
 daemon-reload|show-environment) ;;
 *) exit 2 ;;
esac
"""); self.systemctl.chmod(0o755)
  self.httpd=socketserver.TCPServer(("127.0.0.1",0),ReadyHandler)
  threading.Thread(target=self.httpd.serve_forever,daemon=True).start()
  self.addCleanup(self.httpd.server_close); self.addCleanup(self.httpd.shutdown)
  self.env=os.environ.copy(); self.env.update({"HOME":str(self.home),"PATH":f"{self.bin}:{os.environ['PATH']}",
   "ENSEMBLE_INSTALL_ROOT":str(self.root),"ENSEMBLE_SYSTEMCTL_BIN":str(self.systemctl),"ENSEMBLE_USER_UNIT_DIR":str(self.units_dir),
   "ENSEMBLE_READY_URL":f"http://127.0.0.1:{self.httpd.server_address[1]}/ready","SYSTEMCTL_LOG":str(self.log),
   "SYSTEMCTL_STATE":str(self.state),"DEPLOY_ROOT":str(self.root)})
  self.state.write_text("")
  (self.root/"shared"/"activation-backups").mkdir(parents=True)
  for d in ("claude-code","config","data","logs"): (self.root/"shared"/d).mkdir(exist_ok=True)
  for version in ("0.1","0.2"):
   release=self.root/"releases"/version; (release/"tools").mkdir(parents=True)
   shutil.copy2(DEPLOYMENT_SOURCE,release/"tools"/"ce-deployment")
   (release/"claude-code").symlink_to("../../shared/claude-code")
   installer=release/"install.sh"
   installer.write_text("#!/usr/bin/env bash\nset -euo pipefail\nunit_dir=\"$ENSEMBLE_USER_UNIT_DIR\"\nmkdir -p \"$unit_dir\"\nfor unit in "+" ".join(UNITS)+"; do printf 'ExecStart=%s\\n' \"$1\" > \"$unit_dir/$unit\"; done\n")
   installer.chmod(0o755)
 def run_deployment(self,*args,check=True,extra_env=None):
  env=self.env.copy()
  if extra_env: env.update(extra_env)
  p=subprocess.run(["bash",str(self.root/"releases"/"0.1"/"tools"/"ce-deployment"),*args],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
  if check and p.returncode: self.fail(f"command failed ({p.returncode}): {p.stdout}")
  return p
 def test_failed_upgrade_rolls_back_and_interrupted_recovery_restores_previous(self):
  self.run_deployment("activate","0.1")
  self.assertEqual(os.readlink(self.root/"current"),"releases/0.1")
  self.assertEqual(set(self.state.read_text().splitlines()),set(UNITS))
  failed=self.run_deployment("activate","0.2",check=False,extra_env={"FAIL_VERSION":"0.2"})
  self.assertNotEqual(failed.returncode,0); self.assertEqual(os.readlink(self.root/"current"),"releases/0.1",failed.stdout)
  self.assertFalse((self.root/"activation.json").exists()); self.assertEqual(set(self.state.read_text().splitlines()),set(UNITS))
  known=json.loads((self.root/"known-good.json").read_text()); self.assertEqual((known["active"],known["previous"]),("0.1",None))
  rejected=self.run_deployment("rollback",check=False); self.assertNotEqual(rejected.returncode,0); self.assertIn("no previous known-good release",rejected.stdout)
  self.run_deployment("activate","0.2")
  known=json.loads((self.root/"known-good.json").read_text()); self.assertEqual((known["active"],known["previous"]),("0.2","0.1"))
  self.run_deployment("rollback"); self.assertEqual(os.readlink(self.root/"current"),"releases/0.1")
  known=json.loads((self.root/"known-good.json").read_text()); self.assertEqual((known["active"],known["previous"]),("0.1","0.2"))
  backup=self.root/"shared"/"activation-backups"/"activation.test"; backup.mkdir()
  active_file=backup/"active-before.txt"; active_file.write_text("".join(u+chr(10) for u in UNITS))
  for unit in UNITS: shutil.copy2(self.units_dir/unit,backup/unit)
  shutil.copy2(self.root/"known-good.json",backup/"known-good-before.json")
  (self.root/"activation.json").write_text(json.dumps({"schema_version":1,"previous":"0.1","target":"0.2","unit_backup":str(backup),"active_before_file":str(active_file)}))
  tmp=self.root/".current-test"; tmp.symlink_to("releases/0.2"); tmp.replace(self.root/"current")
  self.run_deployment("recover")
  self.assertEqual(os.readlink(self.root/"current"),"releases/0.1")
  self.assertFalse((self.root/"activation.json").exists()); self.assertFalse(backup.exists())
  self.assertEqual(set(self.state.read_text().splitlines()),set(UNITS))
 def test_activation_refuses_invalid_current_symlink_before_touching_services(self):
  outside=self.root/"outside"; outside.mkdir()
  current=self.root/"current"; current.symlink_to("releases/../outside")
  result=self.run_deployment("activate","0.1",check=False)
  self.assertNotEqual(result.returncode,0)
  self.assertIn("current symlink is invalid",result.stdout)
  self.assertEqual(os.readlink(current),"releases/../outside")
  self.assertFalse(self.log.exists(),"invalid current link must be rejected before systemctl is invoked")
 def test_validation_rejects_links_into_other_releases(self):
  release=self.root/"releases"/"0.1"
  other=self.root/"releases"/"0.2"
  (other/"sentinel").write_text("other release data",encoding="utf-8")
  (release/"cross-release-link").symlink_to("../0.2/sentinel")
  result=self.run_deployment("validate","0.1",check=False)
  self.assertNotEqual(result.returncode,0)
  self.assertIn("release contains broken links or links into development checkouts",result.stdout)
  self.assertTrue((other/"sentinel").is_file(),"validation must not mutate the target release")
 def test_recovery_rejects_symlinked_previous_release_before_stopping_services(self):
  release=self.root/"releases"/"0.1"
  outside=self.home/"outside-release"
  outside.mkdir()
  (outside/"tools").mkdir()
  shutil.copy2(DEPLOYMENT_SOURCE,outside/"tools"/"ce-deployment")
  installer=outside/"install.sh"
  installer.write_text("#!/usr/bin/env bash\nexit 0\n")
  installer.chmod(0o755)
  shutil.rmtree(release)
  release.symlink_to(outside,target_is_directory=True)
  current=self.root/"current"
  current.symlink_to("releases/0.2")
  backup=self.root/"shared"/"activation-backups"/"unsafe-recovery"
  backup.mkdir()
  active_file=backup/"active-before.txt"
  active_file.write_text("".join(unit+chr(10) for unit in UNITS))
  journal={"schema_version":1,"previous":"0.1","target":"0.2","unit_backup":str(backup),"active_before_file":str(active_file)}
  (self.root/"activation.json").write_text(json.dumps(journal))
  result=self.run_deployment("recover",check=False)
  self.assertNotEqual(result.returncode,0)
  self.assertIn("release is incomplete or is a symlink",result.stdout)
  self.assertEqual(os.readlink(current),"releases/0.2")
  self.assertTrue((self.root/"activation.json").is_file(),"unsafe journal must be retained for inspection")
  self.assertFalse(self.log.exists(),"recovery must reject unsafe release before stopping services")
if __name__=="__main__": unittest.main(verbosity=2)
