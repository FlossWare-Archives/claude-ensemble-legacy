#!/usr/bin/env python3
"""
Model Discovery & Auto-Update
Probes all configured APIs to discover available models and updates Thompson routing.
Runs at session start or manually to keep model lists current.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


class ModelDiscovery:
    def __init__(self, settings_path: Path, thompson_path: Path):
        self.settings_path = Path(settings_path)
        self.thompson_path = Path(thompson_path)
        self.discovered = {}

    def discover_anthropic_models(self) -> List[str]:
        """Discover available Anthropic Claude models"""
        try:
            from anthropic import Anthropic
            client = Anthropic()
            # List models via API
            models = client.models.list()
            claude_models = [m.id for m in models.data if 'claude' in m.id.lower()]
            logger.info(f"✓ Anthropic: Found {len(claude_models)} Claude models")
            return sorted(claude_models, reverse=True)  # Latest first
        except Exception as e:
            logger.warning(f"✗ Anthropic discovery failed: {e}")
            # Fallback to known latest
            return ['claude-opus-5-5', 'claude-sonnet-5', 'claude-haiku-4-5-20251001']

    def discover_google_models(self, api_key: str) -> Dict[str, List[str]]:
        """Discover available Google Gemini models"""
        models = {'gemini': [], 'vertex': []}
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)

            all_models = [m.name.split('/')[-1] for m in genai.list_models()]
            # Filter for stable Gemini models (exclude experimental, veo, etc)
            gemini_models = [m for m in all_models if 'gemini' in m.lower() and 'exp' not in m.lower()]
            logger.info(f"✓ Gemini: Found {len(gemini_models)} stable models")
            models['gemini'] = sorted(gemini_models, reverse=True)  # Latest first
        except Exception as e:
            logger.warning(f"✗ Gemini discovery failed: {e}")
            models['gemini'] = ['gemini-2.0-pro', 'gemini-2.0-flash', 'gemini-1.5-pro']

        return models

    def discover_cursor_models(self, api_key: str) -> List[str]:
        """Discover available Cursor models (Claude-compatible)"""
        try:
            # Cursor API is Claude-compatible
            # Try to infer from API behavior or use defaults
            logger.info("✓ Cursor: Using API defaults (routes to latest)")
            return ['cursor-pro', 'cursor']  # Pro first, then default
        except Exception as e:
            logger.warning(f"✗ Cursor discovery failed: {e}")
            return ['cursor']

    def get_best_models(self) -> Dict[str, str]:
        """Select best model from each provider"""
        anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        google_api_key = os.environ.get('GOOGLE_API_KEY', '')
        cursor_api_key = os.environ.get('CURSOR_API_KEY', '')

        best = {}

        # Anthropic - prefer Opus 5.5
        anthropic_models = self.discover_anthropic_models()
        for model in anthropic_models:
            if 'opus-5-5' in model:
                best['opus'] = model
                break
        if 'opus' not in best:
            best['opus'] = anthropic_models[0] if anthropic_models else 'claude-opus-5-5'

        # Sonnet
        for model in anthropic_models:
            if 'sonnet-5' in model and 'opus' not in model:
                best['sonnet'] = model
                break
        if 'sonnet' not in best:
            best['sonnet'] = 'claude-sonnet-5'

        # Haiku
        for model in anthropic_models:
            if 'haiku' in model:
                best['haiku'] = model
                break
        if 'haiku' not in best:
            best['haiku'] = 'claude-haiku-4-5-20251001'

        # Google/Gemini - prefer 2.0-pro > 2.0-flash > 1.5-pro
        if google_api_key:
            gemini_models = self.discover_google_models(google_api_key)
            gemini_list = gemini_models.get('gemini', [])
            # Explicit priority list
            priority = ['gemini-2.0-pro', 'gemini-2.0-flash', 'gemini-1.5-pro']
            for priority_model in priority:
                if any(priority_model in m for m in gemini_list):
                    best['gemini'] = priority_model
                    break
            if 'gemini' not in best:
                best['gemini'] = 'gemini-2.0-pro'
        else:
            best['gemini'] = 'gemini-2.0-pro'

        # Cursor - prefer pro
        if cursor_api_key:
            cursor_models = self.discover_cursor_models(cursor_api_key)
            best['cursor'] = cursor_models[0] if cursor_models else 'cursor'
        else:
            best['cursor'] = 'cursor'

        return best

    def update_thompson_router(self, best_models: Dict[str, str]) -> None:
        """Update Thompson router with discovered models"""
        try:
            with open(self.thompson_path) as f:
                content = f.read()

            # Replace model references (simple string replacement)
            replacements = {
                "'haiku-4-5'": f"'{best_models.get('haiku', 'claude-haiku-4-5-20251001')}'",
                "'sonnet-5'": f"'{best_models.get('sonnet', 'claude-sonnet-5')}'",
                "'opus-5-5'": f"'{best_models.get('opus', 'claude-opus-5-5')}'",
                "'gemini-2.0-pro'": f"'{best_models.get('gemini', 'gemini-2.0-pro')}'",
                "'cursor'": f"'{best_models.get('cursor', 'cursor')}'",
            }

            for old, new in replacements.items():
                content = content.replace(old, new)

            with open(self.thompson_path, 'w') as f:
                f.write(content)

            logger.info(f"✓ Updated Thompson router with {len(best_models)} models")
        except Exception as e:
            logger.error(f"✗ Failed to update Thompson: {e}")

    def update_settings(self, best_models: Dict[str, str]) -> None:
        """Update settings.json with model choices"""
        try:
            with open(self.settings_path) as f:
                settings = json.load(f)

            if 'env' not in settings:
                settings['env'] = {}

            # Store discovered best models
            settings['env']['RH_BEST_MODELS'] = json.dumps(best_models)
            settings['env']['RH_MODELS_DISCOVERED_AT'] = datetime.now(timezone.utc).isoformat()

            with open(self.settings_path, 'w') as f:
                json.dump(settings, f, indent=2)

            logger.info(f"✓ Updated settings.json with model discovery metadata")
        except Exception as e:
            logger.error(f"✗ Failed to update settings: {e}")

    def report(self, best_models: Dict[str, str]) -> None:
        """Print discovery report"""
        print("\n" + "="*70)
        print("  MODEL DISCOVERY REPORT")
        print("="*70)
        print("\nBest available models:")
        for provider, model in sorted(best_models.items()):
            print(f"  {provider:12s} → {model}")
        print("\nThompson routing now uses these for intelligent model selection.")
        print("="*70 + "\n")

    def run(self) -> Dict[str, str]:
        """Discover and update everything"""
        logger.info("Starting model discovery...")
        best_models = self.get_best_models()

        self.update_thompson_router(best_models)
        self.update_settings(best_models)
        self.report(best_models)

        return best_models


if __name__ == '__main__':
    import sys

    rh_tools_root = Path(os.environ.get('RH_TOOLS_ROOT',
                         Path.home() / 'Development/redhat/scm/gitlab/cee/sfloess/claude-global-skills'))

    settings_path = rh_tools_root / 'settings.json'
    thompson_path = rh_tools_root / 'shared/thompson_router.py'

    discoverer = ModelDiscovery(settings_path, thompson_path)
    best_models = discoverer.run()

    sys.exit(0)
