"""Pure waypoint-interpolation math (no Isaac) — plan.md Workstream D math half."""

import math

import pytest

from solar_twin.control.base import Waypoint
from solar_twin.control.kinematic_math import reached, step_towards, steps_to_reach


def test_step_towards_moves_partway_then_reaches():
    start = Waypoint(0.0, 0.0, 0.0)
    target = Waypoint(10.0, 0.0, 0.0)
    step1 = step_towards(start, target, speed=1.0, dt=1.0)
    assert step1.x == pytest.approx(1.0)
    assert not reached(step1, target)

    n = steps_to_reach(start, target, speed=1.0, dt=1.0)
    assert n == 10
    final = start
    for _ in range(n):
        final = step_towards(final, target, speed=1.0, dt=1.0)
    assert reached(final, target)
    assert final.x == pytest.approx(target.x)


def test_step_towards_never_overshoots():
    start = Waypoint(0.0, 0.0, 0.0)
    target = Waypoint(1.0, 0.0, 0.0)
    # A huge speed*dt would overshoot a naive linear extrapolation.
    step1 = step_towards(start, target, speed=100.0, dt=1.0)
    assert step1.x == pytest.approx(target.x)
    assert step1.y == pytest.approx(target.y)
    assert step1.z == pytest.approx(target.z)


def test_step_towards_diagonal_reaches_exactly():
    start = Waypoint(0.0, 0.0, 0.0)
    target = Waypoint(3.0, 4.0, 0.0)  # 3-4-5 triangle, distance 5
    n = steps_to_reach(start, target, speed=1.0, dt=1.0)
    assert n == 5


def test_zero_speed_never_reaches_and_is_capped():
    start = Waypoint(0.0, 0.0, 0.0)
    target = Waypoint(1.0, 0.0, 0.0)
    n = steps_to_reach(start, target, speed=0.0, dt=1.0, max_steps=50)
    assert n == 50  # capped, not an infinite loop


def test_reached_respects_tolerance():
    a = Waypoint(0.0, 0.0, 0.0)
    b = Waypoint(0.04, 0.0, 0.0)
    assert reached(a, b, tol=0.05)
    assert not reached(a, b, tol=0.01)


def test_yaw_turns_shortest_direction_and_wraps():
    # From yaw=170deg turning to yaw=-170deg: shortest path is +20deg, not -340deg.
    start = Waypoint(0.0, 0.0, 0.0, yaw=math.radians(170))
    target = Waypoint(0.0, 0.0, 0.0, yaw=math.radians(-170))
    stepped = step_towards(start, target, speed=0.0, dt=1.0, angular_speed=math.radians(10))
    expected = math.radians(180)  # 170 + 10
    assert stepped.yaw == pytest.approx(expected, abs=1e-9)


def test_yaw_reaches_target_exactly_when_within_turn_budget():
    start = Waypoint(0.0, 0.0, 0.0, yaw=0.0)
    target = Waypoint(0.0, 0.0, 0.0, yaw=math.radians(30))
    stepped = step_towards(start, target, speed=0.0, dt=1.0, angular_speed=math.pi)
    assert stepped.yaw == pytest.approx(math.radians(30))
