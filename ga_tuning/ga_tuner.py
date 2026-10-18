#!/usr/bin/env python3
"""
Genetic Algorithm Tuning System for 5 RH Fixed Tools

Evolves parameters for:
1. Compression GA - compression_level, target_reduction
2. Thompson Router GA - alpha_prior, beta_prior, cost_weight
3. Caching GA - ttl_seconds, cache_threshold
4. Capability Matrix GA - ScoringWeights (domain, complexity, task)
5. Dashboard GA - learning_rate, exploration_decay, alert_threshold

Local evaluation only (no API calls). Uses parallel fitness evaluation.
"""

import copy
import json
import os
import sys
import time
import logging
import random
import secrets
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, asdict, field
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import yaml

# Add evaluators to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'evaluators'))

from compression_evaluator import CompressionEvaluator
from thompson_evaluator import ThompsonEvaluator
from caching_evaluator import CachingEvaluator
from matrix_evaluator import MatrixEvaluator
from dashboard_evaluator import DashboardEvaluator
from parameter_schema import bounds_for_system

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# GENETIC ALGORITHM FRAMEWORK
# ============================================================================

@dataclass
class GAConfig:
    """GA configuration"""
    population_size: int = 50
    generations: int = 25
    crossover_rate: float = 0.8
    mutation_rate: float = 0.1
    tournament_k: int = 3
    # None gives each scheduled run a fresh seed; set an integer for reproducible experiments.
    seed: int | None = None
    max_workers: int = 4
    output_dir: str = './results'


@dataclass
class Individual:
    """Single individual (parameter set)"""
    system_name: str
    parameters: Dict[str, float]
    fitness: Optional[float] = None
    generation: int = 0


class GeneticAlgorithm:
    """Main GA loop for parameter optimization"""

    def __init__(self, config: GAConfig):
        self.config = config
        self.population: List[Individual] = []
        # Fitness is evaluator-specific and cannot be compared across systems.
        self.best_by_system: Dict[str, Individual] = {}
        self.fitness_history: List[Dict[str, Any]] = []
        self.evaluators: Dict[str, Any] = {}
        self.total_evaluations = 0

        # Create output directory
        Path(config.output_dir).mkdir(parents=True, exist_ok=True)

        self.seed = config.seed if config.seed is not None else secrets.randbits(32)
        random.seed(self.seed)
        np.random.seed(self.seed)
        logger.info("Using GA seed=%s%s", self.seed, " (explicit/reproducible)" if config.seed is not None else " (generated per run)")

    def initialize_evaluators(self):
        """Initialize all 5 evaluators"""
        logger.info("Initializing evaluators...")
        rh_memory_dir = Path(os.environ.get('CE_MEMORY_DIR', Path.home() / 'Development/redhat/scm/gitlab/cee/sfloess/claude-global-skills/memory')).expanduser()

        self.evaluators['compression'] = CompressionEvaluator(rh_memory_dir)
        self.evaluators['thompson'] = ThompsonEvaluator()
        self.evaluators['caching'] = CachingEvaluator()
        self.evaluators['matrix'] = MatrixEvaluator(rh_memory_dir)
        self.evaluators['dashboard'] = DashboardEvaluator()

        logger.info(f"Initialized {len(self.evaluators)} evaluators")

    def get_parameter_bounds(self, system_name: str) -> Dict[str, Tuple[float, float]]:
        """Get optimizer bounds from the shared parameter schema."""
        return bounds_for_system(system_name)

    def create_random_individual(self, system_name: str, generation: int = 0) -> Individual:
        """Create individual with random parameters"""
        bounds = self.get_parameter_bounds(system_name)
        parameters = {}

        for param_name, (lower, upper) in bounds.items():
            parameters[param_name] = random.uniform(lower, upper)

        return Individual(system_name=system_name, parameters=parameters, generation=generation)

    def initialize_population(self):
        """Create initial population for all 5 systems"""
        logger.info(f"Creating initial population of {self.config.population_size}")
        self.population = []
        systems = ['compression', 'thompson', 'caching', 'matrix', 'dashboard']
        if self.config.tournament_k < 1:
            raise ValueError("tournament_k must be at least 1")
        minimum_population = len(systems) * self.config.tournament_k
        if self.config.population_size < minimum_population:
            raise ValueError(
                f"population_size={self.config.population_size} is too small for "
                f"{len(systems)} systems with tournament_k={self.config.tournament_k}; "
                f"need at least {minimum_population}"
            )

        per_system, remainder = divmod(self.config.population_size, len(systems))
        for index, system in enumerate(systems):
            count = per_system + (1 if index < remainder else 0)
            for _ in range(count):
                self.population.append(self.create_random_individual(system))

        logger.info(f"Created {len(self.population)} individuals")

    def evaluate_individual(self, individual: Individual) -> float:
        """Evaluate fitness of an individual"""
        try:
            evaluator = self.evaluators[individual.system_name]
            fitness = evaluator.evaluate(individual.parameters)
            return fitness
        except Exception as e:
            logger.error(f"Error evaluating {individual.system_name}: {e}")
            return 0.0

    def evaluate_population(self):
        """Evaluate entire population in parallel"""
        logger.info(f"Evaluating {len(self.population)} individuals...")

        # Sequential evaluation for now (can be parallelized)
        for i, individual in enumerate(self.population):
            if individual.fitness is None:
                fitness = self.evaluate_individual(individual)
                individual.fitness = fitness
                self.total_evaluations += 1
                if (i + 1) % 10 == 0:
                    logger.info(f"  Evaluated {i+1}/{len(self.population)}")

        # Retain a detached historical best per evaluator. Cross-system fitness
        # values are never compared.
        for system in sorted({ind.system_name for ind in self.population}):
            candidates = [
                ind for ind in self.population
                if ind.system_name == system and ind.fitness is not None
            ]
            if not candidates:
                continue
            candidate = max(candidates, key=lambda ind: ind.fitness)
            previous = self.best_by_system.get(system)
            if previous is None or candidate.fitness > previous.fitness:
                self.best_by_system[system] = copy.deepcopy(candidate)
                logger.info("New best for %s: fitness=%.6f", system, candidate.fitness)

    def tournament_selection(self, system_name: str, k: int = 3) -> Individual:
        """Select an individual via tournament selection within one evaluator."""
        candidates = [
            ind for ind in self.population if ind.system_name == system_name
        ]
        if k < 1 or len(candidates) < k:
            raise ValueError(
                f"Cannot run tournament for {system_name}: need {k} candidates, "
                f"have {len(candidates)}"
            )
        tournament = random.sample(candidates, k)
        if any(ind.fitness is None for ind in tournament):
            raise ValueError(f"Cannot select {system_name} candidates before evaluation")
        return max(tournament, key=lambda ind: ind.fitness)

    def crossover(self, parent1: Individual, parent2: Individual) -> Tuple[Individual, Individual]:
        """Single-point crossover"""
        if parent1.system_name != parent2.system_name:
            raise ValueError(
                f"Cannot crossover different systems: "
                f"{parent1.system_name} vs {parent2.system_name}"
            )

        # Even when crossover is skipped, offspring must not alias parents.
        if random.random() > self.config.crossover_rate:
            return copy.deepcopy(parent1), copy.deepcopy(parent2)

        param_names = list(parent1.parameters.keys())
        crossover_point = random.randint(0, len(param_names) - 1)

        child1_params = {}
        child2_params = {}

        for i, param in enumerate(param_names):
            if i < crossover_point:
                child1_params[param] = parent1.parameters[param]
                child2_params[param] = parent2.parameters[param]
            else:
                child1_params[param] = parent2.parameters[param]
                child2_params[param] = parent1.parameters[param]

        child1 = Individual(
            system_name=parent1.system_name, parameters=child1_params,
            generation=max(parent1.generation, parent2.generation)
        )
        child2 = Individual(
            system_name=parent1.system_name, parameters=child2_params,
            generation=max(parent1.generation, parent2.generation)
        )

        return child1, child2

    def mutate(self, individual: Individual):
        """Gaussian mutation; any selected mutation invalidates cached fitness."""
        bounds = self.get_parameter_bounds(individual.system_name)
        mutated = False

        for param_name in list(individual.parameters):
            if random.random() < self.config.mutation_rate:
                lower, upper = bounds[param_name]
                current = individual.parameters[param_name]
                mutation_std = (upper - lower) * 0.1
                new_value = np.random.normal(current, mutation_std)
                individual.parameters[param_name] = float(np.clip(new_value, lower, upper))
                mutated = True

        if mutated:
            individual.fitness = None

    def evolve(self, generation: int):
        """Evolve each evaluator population independently with detached elites."""
        logger.info("\n=== Generation %s/%s ===", generation + 1, self.config.generations)
        self.evaluate_population()

        by_system = {}
        for system in sorted({ind.system_name for ind in self.population}):
            members = [ind for ind in self.population if ind.system_name == system]
            scores = [ind.fitness for ind in members if ind.fitness is not None]
            if len(scores) != len(members):
                raise RuntimeError(f"Population for {system} contains unevaluated candidates")
            by_system[system] = {
                "best": max(scores),
                "mean": float(np.mean(scores)),
                "std": float(np.std(scores)),
                "worst": min(scores),
                "population_size": len(members),
                "best_individual": asdict(max(members, key=lambda ind: ind.fitness)),
            }
        self.fitness_history.append({
            "generation": generation,
            "systems": by_system,
        })

        new_population = []
        for system in sorted(by_system):
            members = [ind for ind in self.population if ind.system_name == system]
            elite = max(members, key=lambda ind: ind.fitness)
            # Immutable snapshot: elite is detached and never sent through mutation.
            elite_copy = copy.deepcopy(elite)
            elite_copy.generation = generation + 1
            new_population.append(elite_copy)

            children = 1
            while children < len(members):
                parent1 = self.tournament_selection(system, self.config.tournament_k)
                parent2 = self.tournament_selection(system, self.config.tournament_k)
                child1, child2 = self.crossover(parent1, parent2)
                self.mutate(child1)
                self.mutate(child2)
                child1.generation = generation + 1
                child2.generation = generation + 1
                new_population.append(child1)
                children += 1
                if children < len(members):
                    new_population.append(child2)
                    children += 1

        if len(new_population) != len(self.population):
            raise RuntimeError("Evolution changed total population size")
        for system in by_system:
            before = sum(ind.system_name == system for ind in self.population)
            after = sum(ind.system_name == system for ind in new_population)
            if before != after:
                raise RuntimeError(f"Evolution changed {system} population size: {before} -> {after}")
        self.population = new_population

    def run(self):
        """Run complete GA"""
        logger.info("=" * 80)
        logger.info("GENETIC ALGORITHM TUNING SYSTEM FOR 5 RH TOOLS")
        logger.info("=" * 80)
        logger.info(f"Configuration: {self.config}")

        self.initialize_evaluators()
        self.initialize_population()

        start_time = time.time()

        for generation in range(self.config.generations):
            self.evolve(generation)

        # The final offspring have not been evaluated by evolve() yet.
        self.evaluate_population()
        elapsed = time.time() - start_time

        logger.info("\n" + "=" * 80)
        logger.info("OPTIMIZATION COMPLETE")
        logger.info("=" * 80)
        logger.info(f"Elapsed time: {elapsed:.2f} seconds")
        logger.info("Best fitness by system:")
        for system, individual in sorted(self.best_by_system.items()):
            logger.info("  %s: %.6f %s", system, individual.fitness, individual.parameters)

        return self.save_results()

    def save_results(self) -> Dict[str, Any]:
        """Save results to JSON files"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        # Save best parameters for each system
        results_by_system = {}
        for ind in self.population:
            if ind.fitness is None:
                continue  # Skip individuals without fitness
            if ind.system_name not in results_by_system:
                results_by_system[ind.system_name] = []
            results_by_system[ind.system_name].append({
                'parameters': {k: float(v) for k, v in ind.parameters.items()},
                'fitness': float(ind.fitness),
            })

        # Keep best 3 for each system
        best_params = {}
        for system, individuals in results_by_system.items():
            if not individuals:
                continue
            sorted_inds = sorted(individuals, key=lambda x: x['fitness'], reverse=True)
            best_params[system] = sorted_inds[0:3]

        # Save best parameters
        best_file = os.path.join(self.config.output_dir, f'ga_best_parameters_{timestamp}.json')
        with open(best_file, 'w') as f:
            json.dump(best_params, f, indent=2)
        logger.info(f"Saved best parameters to {best_file}")

        # Save fitness history
        history_file = os.path.join(self.config.output_dir, f'ga_fitness_history_{timestamp}.json')
        with open(history_file, 'w') as f:
            json.dump(self.fitness_history, f, indent=2)
        logger.info(f"Saved fitness history to {history_file}")

        # Save summary report
        summary = {
            'timestamp': timestamp,
            'seed': self.seed,
            'total_evaluations': self.total_evaluations,
            'generations': self.config.generations,
            'population_size': self.config.population_size,
            'best_by_system': best_params,
        }

        summary_file = os.path.join(self.config.output_dir, f'ga_summary_{timestamp}.json')
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        logger.info(f"Saved summary to {summary_file}")

        return summary


def load_config(config_file: Path | None = None) -> GAConfig:
    """Load the shipped GA configuration, retaining support for a legacy CWD file."""
    if config_file is None:
        shipped_config = Path(__file__).resolve().parent / "config" / "ga_config.yaml"
        legacy_config = Path.cwd() / "ga_config.yaml"
        config_file = shipped_config if shipped_config.is_file() else legacy_config

    if not config_file.is_file():
        return GAConfig()

    with config_file.open("r", encoding="utf-8") as f:
        config_dict = yaml.safe_load(f) or {}
    if not isinstance(config_dict, dict):
        raise ValueError(f"GA config must be a YAML mapping: {config_file}")

    # System bounds and descriptions document parameter_schema.py; they are not
    # GAConfig constructor fields and must not be passed into the dataclass.
    config_dict.pop("systems", None)
    return GAConfig(**config_dict)


def main():
    """Main entry point"""
    config = load_config()
    # The scheduled runner supplies a durable, deployment-owned output path.
    # Explicit config remains the default for direct development runs.
    config.output_dir = os.environ.get("CE_GA_RESULTS_DIR", config.output_dir)
    ga = GeneticAlgorithm(config)
    summary = ga.run()

    print("\n" + "=" * 80)
    print("BEST PARAMETERS BY SYSTEM")
    print("=" * 80)
    for system, individuals in summary['best_by_system'].items():
        print(f"\n{system.upper()}:")
        for i, ind in enumerate(individuals, 1):
            print(f"  Rank {i} (fitness={ind['fitness']:.6f}):")
            for param, value in ind['parameters'].items():
                print(f"    {param}: {value:.4f}")


if __name__ == '__main__':
    main()
