import copy
from unittest.mock import patch

from ga_tuner import GAConfig, GeneticAlgorithm, Individual


class FakeEvaluator:
    def __init__(self):
        self.calls = 0

    def evaluate(self, parameters):
        self.calls += 1
        return parameters["x"]


def make_ga(tmp_path, population, mutation_rate=1.0):
    config = GAConfig(
        population_size=len(population),
        generations=1,
        crossover_rate=0.0,
        mutation_rate=mutation_rate,
        tournament_k=2,
        seed=42,
        output_dir=str(tmp_path),
    )
    ga = GeneticAlgorithm(config)
    ga.evaluators = {system: FakeEvaluator() for system in ("alpha", "beta")}
    ga.get_parameter_bounds = lambda system: {"x": (0.0, 10.0)}
    ga.population = population
    return ga


def test_tournament_selection_is_system_scoped(tmp_path):
    ga = make_ga(
        tmp_path,
        [
            Individual("alpha", {"x": 1.0}, fitness=0.1),
            Individual("alpha", {"x": 2.0}, fitness=0.2),
            Individual("beta", {"x": 10.0}, fitness=100.0),
        ],
    )

    selected = ga.tournament_selection("alpha", k=2)

    assert selected.system_name == "alpha"


def test_crossover_rejects_different_systems(tmp_path):
    ga = make_ga(
        tmp_path,
        [
            Individual("alpha", {"x": 2.0}, fitness=2.0),
            Individual("beta", {"x": 1.0}, fitness=1.0),
        ],
    )

    try:
        ga.crossover(*ga.population)
    except ValueError as exc:
        assert "Cannot crossover different systems" in str(exc)
    else:
        raise AssertionError("Expected cross-system crossover to be rejected")


def test_crossover_without_crossover_clones_parents(tmp_path):
    ga = make_ga(
        tmp_path,
        [
            Individual("alpha", {"x": 2.0}, fitness=2.0),
            Individual("alpha", {"x": 1.0}, fitness=1.0),
        ],
        mutation_rate=0.0,
    )
    parent1, parent2 = ga.population
    child1, child2 = ga.crossover(parent1, parent2)

    assert child1 is not parent1
    assert child2 is not parent2
    assert child1.parameters == parent1.parameters
    assert child2.parameters == parent2.parameters
    child1.parameters["x"] = 3.0
    assert parent1.parameters["x"] == 2.0


def test_elite_is_preserved_unchanged(tmp_path):
    population = [
        Individual("alpha", {"x": 9.0}, fitness=9.0),
        Individual("alpha", {"x": 5.0}, fitness=5.0),
        Individual("alpha", {"x": 1.0}, fitness=1.0),
    ]
    ga = make_ga(tmp_path, population)
    elite_before = copy.deepcopy(population[0])

    ga.evolve(0)

    elite_after = next(ind for ind in ga.population if ind.generation == 1 and ind.parameters == {"x": 9.0})

    assert elite_after is not population[0]
    assert elite_after.parameters == elite_before.parameters
    assert elite_after.fitness == elite_before.fitness
    assert elite_after.generation == 1
    assert population[0].parameters == elite_before.parameters


def test_generation_preserves_system_population_sizes(tmp_path):
    population = [
        Individual("alpha", {"x": 1.0}, fitness=1.0),
        Individual("alpha", {"x": 2.0}, fitness=2.0),
        Individual("alpha", {"x": 3.0}, fitness=3.0),
        Individual("beta", {"x": 3.0}, fitness=3.0),
        Individual("beta", {"x": 4.0}, fitness=4.0),
    ]
    ga = make_ga(tmp_path, population)

    ga.evolve(0)

    assert sum(ind.system_name == "alpha" for ind in ga.population) == 3
    assert sum(ind.system_name == "beta" for ind in ga.population) == 2


def test_mutation_invalidates_stale_fitness(tmp_path):
    ga = make_ga(
        tmp_path,
        [Individual("alpha", {"x": 5.0}, fitness=5.0)],
    )
    individual = ga.population[0]

    ga.mutate(individual)

    assert individual.fitness is None


def test_best_candidates_are_tracked_per_system(tmp_path):
    ga = make_ga(
        tmp_path,
        [
            Individual("alpha", {"x": 2.0}, fitness=2.0),
            Individual("alpha", {"x": 1.0}, fitness=1.0),
            Individual("beta", {"x": 10.0}, fitness=100.0),
            Individual("beta", {"x": 8.0}, fitness=80.0),
        ],
    )

    ga.evaluate_population()

    assert ga.best_by_system["alpha"].system_name == "alpha"
    assert ga.best_by_system["alpha"].fitness == 2.0
    assert ga.best_by_system["beta"].fitness == 100.0
    assert ga.best_by_system["alpha"] is not ga.population[0]

    # Historical best must remain detached if the live population later changes.
    ga.population[0].parameters["x"] = 0.0
    ga.population[0].fitness = 0.0
    assert ga.best_by_system["alpha"].parameters["x"] == 2.0
    assert ga.best_by_system["alpha"].fitness == 2.0


def test_population_validation_requires_tournament_capacity(tmp_path):
    ga = GeneticAlgorithm(
        GAConfig(
            population_size=10,
            generations=1,
            tournament_k=3,
            output_dir=str(tmp_path),
        )
    )

    try:
        ga.initialize_population()
    except ValueError as exc:
        assert "need at least 15" in str(exc)
    else:
        raise AssertionError("Expected invalid population size to be rejected")


def test_population_initialization_distributes_remainder(tmp_path):
    ga = GeneticAlgorithm(
        GAConfig(
            population_size=16,
            generations=1,
            tournament_k=3,
            output_dir=str(tmp_path),
        )
    )
    ga.get_parameter_bounds = lambda system: {"x": (0.0, 10.0)}

    ga.initialize_population()

    counts = {system: sum(ind.system_name == system for ind in ga.population)
              for system in ("compression", "thompson", "caching", "matrix", "dashboard")}
    assert len(ga.population) == 16
    assert sorted(counts.values()) == [3, 3, 3, 3, 4]


def test_run_evaluates_final_offspring_before_saving(tmp_path):
    ga = GeneticAlgorithm(
        GAConfig(
            population_size=15,
            generations=1,
            crossover_rate=0.0,
            mutation_rate=1.0,
            tournament_k=2,
            seed=17,
            output_dir=str(tmp_path),
        )
    )
    ga.initialize_evaluators = lambda: setattr(
        ga, "evaluators", {
            system: FakeEvaluator()
            for system in ("compression", "thompson", "caching", "matrix", "dashboard")
        }
    )
    ga.get_parameter_bounds = lambda system: {"x": (0.0, 10.0)}

    # Make every mutated offspring a known winner; elites remain untouched.
    def force_superior_offspring(individual):
        individual.parameters["x"] = 10.0
        individual.fitness = None

    ga.mutate = force_superior_offspring
    summary = ga.run()

    assert summary["total_evaluations"] == 25
    assert sum(evaluator.calls for evaluator in ga.evaluators.values()) == 25
    assert all(
        any(candidate["parameters"]["x"] == 10.0 for candidate in candidates)
        for candidates in summary["best_by_system"].values()
    )
    assert set(summary["best_by_system"]) == {
        "compression", "thompson", "caching", "matrix", "dashboard"
    }
    for individual in ga.population:
        assert individual.fitness == individual.parameters["x"]
        assert individual.fitness is not None


def test_default_seed_is_generated_per_run(tmp_path):
    with patch("ga_tuner.secrets.randbits", side_effect=[101, 202]):
        first = GeneticAlgorithm(GAConfig(output_dir=str(tmp_path / "first")))
        second = GeneticAlgorithm(GAConfig(output_dir=str(tmp_path / "second")))
    assert first.seed == 101
    assert second.seed == 202


def test_explicit_seed_remains_reproducible(tmp_path):
    first = GeneticAlgorithm(GAConfig(seed=42, output_dir=str(tmp_path / "first")))
    second = GeneticAlgorithm(GAConfig(seed=42, output_dir=str(tmp_path / "second")))
    assert first.seed == second.seed == 42
