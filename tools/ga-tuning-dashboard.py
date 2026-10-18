#!/usr/bin/env python3
"""
GA Tuning Dashboard

Shows genetic algorithm optimization progress:
- Fitness trends over generations
- Parameter evolution (what's changing?)
- Best fitness by evaluator (compression, Thompson, caching, etc.)
"""

import json
import os
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))


class GATuningDashboard:
    """Dashboard for GA tuning progress"""

    def __init__(self, results_dir: Path = None):
        if results_dir is None:
            configured = os.environ.get("CE_GA_RESULTS_DIR")
            if configured:
                results_dir = Path(configured)
            else:
                deploy_root = Path(os.environ.get(
                    "ENSEMBLE_INSTALL_ROOT",
                    Path.home() / ".FlossWare" / "claude-ensemble",
                ))
                deployed_results = deploy_root / "shared" / "data" / "ga-tuning" / "results"
                if (deploy_root / "current").is_symlink() or deployed_results.exists():
                    results_dir = deployed_results
                else:
                    # Direct development runs retain their repository-local default.
                    results_dir = Path(__file__).parent.parent / "ga_tuning" / "results"
        self.results_dir = Path(results_dir)

    def get_latest_results(self) -> Tuple[Path, Path, Path]:
        """Get latest GA results files"""
        best_files = sorted(self.results_dir.glob("ga_best_parameters_*.json"), reverse=True)
        fitness_files = sorted(self.results_dir.glob("ga_fitness_history_*.json"), reverse=True)
        summary_files = sorted(self.results_dir.glob("ga_summary_*.json"), reverse=True)

        if not best_files or not fitness_files or not summary_files:
            return None, None, None

        return best_files[0], fitness_files[0], summary_files[0]

    def parse_fitness_history(self, fitness_file: Path) -> Dict:
        """Parse fitness history and extract trends"""
        try:
            with open(fitness_file) as f:
                history = json.load(f)
        except:
            return {}

        if not history:
            return {}

        # History is a list of generation records
        if isinstance(history, list):
            best_fitness_per_gen = [gen.get('best', 0) for gen in history]
            mean_fitness_per_gen = [gen.get('mean', 0) for gen in history]
        else:
            best_fitness_per_gen = history.get('best_fitness_per_generation', [])
            mean_fitness_per_gen = history.get('mean_fitness_per_generation', [])

        return {
            'total_generations': len(best_fitness_per_gen),
            'best_per_gen': best_fitness_per_gen,
            'mean_per_gen': mean_fitness_per_gen,
            'final_best': best_fitness_per_gen[-1] if best_fitness_per_gen else 0,
            'initial_best': best_fitness_per_gen[0] if best_fitness_per_gen else 0,
        }

    def parse_best_parameters(self, best_file: Path) -> Dict:
        """Parse best parameters by evaluator"""
        try:
            with open(best_file) as f:
                best = json.load(f)
        except:
            return {}

        result = {}
        for evaluator_name, individuals in best.items():
            if individuals and len(individuals) > 0:
                best_individual = individuals[0]
                result[evaluator_name] = {
                    'fitness': best_individual.get('fitness', 0),
                    'parameters': {k: v for k, v in best_individual.items() if k != 'fitness'}
                }

        return result

    def parse_summary(self, summary_file: Path) -> Dict:
        """Parse GA summary"""
        try:
            with open(summary_file) as f:
                return json.load(f)
        except:
            return {}

    def print_report(self):
        """Print formatted dashboard"""
        best_file, fitness_file, summary_file = self.get_latest_results()

        if not best_file:
            print("No GA results found yet.")
            return

        fitness = self.parse_fitness_history(fitness_file)
        best_params = self.parse_best_parameters(best_file)
        summary = self.parse_summary(summary_file)

        print("\n" + "="*80)
        print("  GA TUNING DASHBOARD")
        print("="*80)

        # Summary
        if summary:
            print(f"\nOPTIMIZATION SUMMARY")
            print("-" * 80)
            print(f"Elapsed Time: {summary.get('elapsed_time_seconds', 0):.1f}s")
            print(f"Population: {summary.get('population_size', 'N/A')}")
            print(f"Generations: {summary.get('generations', 'N/A')}")
            print(f"Best Fitness: {summary.get('best_fitness', 0):.6f}")
            print(f"Mean Fitness: {summary.get('mean_fitness', 0):.6f}")
            print(f"Std Fitness: {summary.get('std_fitness', 0):.6f}")
            print(f"Best System: {summary.get('best_system', 'N/A')}")

        # Fitness progression
        if fitness:
            print(f"\nFITNESS PROGRESSION")
            print("-" * 80)
            print(f"Total Generations: {fitness['total_generations']}")
            print(f"Initial Best: {fitness['initial_best']:.6f}")
            print(f"Final Best: {fitness['final_best']:.6f}")
            improvement = fitness['final_best'] - fitness['initial_best']
            print(f"Improvement: {improvement:+.6f} ({100*improvement/fitness['initial_best'] if fitness['initial_best'] > 0 else 0:+.1f}%)")

            # Show fitness trend every 5 generations
            if fitness['best_per_gen']:
                print("\nBest Fitness per Generation (every 5th):")
                for i in range(0, len(fitness['best_per_gen']), max(1, len(fitness['best_per_gen']) // 10)):
                    gen_num = i + 1
                    gen_fitness = fitness['best_per_gen'][i]
                    bar_width = int(gen_fitness * 40)
                    bar = '█' * bar_width
                    print(f"  Gen {gen_num:3d}: {gen_fitness:.4f} {bar}")

        # Best parameters by evaluator
        if best_params:
            print(f"\nBEST FITNESS BY EVALUATOR")
            print("-" * 80)
            for evaluator in sorted(best_params.keys()):
                data = best_params[evaluator]
                fitness_val = data['fitness']
                params = data['parameters']

                print(f"\n{evaluator}:")
                print(f"  Fitness: {fitness_val:.6f}")
                print(f"  Parameters:")
                for param_name, param_value in sorted(params.items()):
                    if isinstance(param_value, float):
                        print(f"    {param_name:30s}: {param_value:.4f}")
                    else:
                        print(f"    {param_name:30s}: {param_value}")

        print("\n" + "="*80 + "\n")


if __name__ == '__main__':
    dashboard = GATuningDashboard()
    dashboard.print_report()
