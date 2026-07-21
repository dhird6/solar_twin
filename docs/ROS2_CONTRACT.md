# ROS2_CONTRACT — the sim↔real Transport seam (bible §6.3)

> **Status: draft, written on the normal machine (Track N), not yet
> implemented.** This defines exactly what `transport/ros2_bridge.py` must
> satisfy so implementation doesn't have to guess message shapes or QoS. **Do
> not build `ros2_bridge.py` against this until Track S's WS0 Day-1 camera
> check has passed** (`docs/ENVIRONMENT.md`) — Slice 0 stays sim-native
> either way; this seam is validated once ROS 2 is proven, not depended on
> before then (`plan.md` Workstream E, `CLAUDE.md` golden rule #5).

## 1. Why this exists

`Transport` (`transport/base.py`) is the one interface with two real
implementations in Slice 0: `sim_native.py` (default, in-process) and
`ros2_bridge.py` (topics). Both must carry **exactly the same information** so
the orchestrator (`orchestrator/mission.py`) never knows or cares which one is
underneath (bible §2.4). This doc is the contract `ros2_bridge.py` has to
implement to be a drop-in swap.

## 2. Topic table (bible §6.3)

| Topic | Type | Dir (from sim) | QoS |
|---|---|---|---|
| `/<robot_ns>/camera/image_raw` | `sensor_msgs/Image` | pub | **Sensor Data (Best Effort)** |
| `/<robot_ns>/camera/camera_info` | `sensor_msgs/CameraInfo` | pub | Sensor Data |
| `/<robot_ns>/cmd_vel` | `geometry_msgs/Twist` | sub | Reliable |
| `/<robot_ns>/pose` | `geometry_msgs/PoseStamped` | pub | Reliable |
| `/mission/fault` | custom (start: `std_msgs/String`, JSON body) | pub | Reliable |
| `/clock` | `rosgraph_msgs/Clock` | pub | — (sim time, if `use_sim_time` is set) |

`<robot_ns>` is one namespace per robot id in `mission.yaml`'s `fleet:` block
(`ground_bot`, `screen_drone`, `confirm_drone` — see §4).

## 3. `/mission/fault` payload = `FaultReport`

Locked now that `schema/pv_module.py`'s `FaultReport` dataclass exists (Track N,
`docs/TASKS.md` N1) — the run record's `fault_events` and this topic serialize
**the same shape**, so `run.py` and `ros2_bridge.py` never drift independently.

Slice-0 transport: `std_msgs/String` whose `data` field is `FaultReport.to_dict()`
JSON-encoded (upgrade path: a real `custom/FaultReport.msg` once this seam is
built and proven — not needed for Slice 0).

```json
{
  "panel_id": "R00-C002",
  "fault_type": "soiled",
  "confidence": 1.0,
  "note": "ground-truth confirm soiled",
  "timestamp": "2026-07-21T10:27:53+00:00",
  "panel_geo_position": [33.4484, -112.07395262881327, 331.0]
}
```

Field notes:
- `fault_type` — one of the fault taxonomy (§6.5): `healthy · soiled · hotspot
  · crack · string_dropout · diode_fault · shading · unknown`.
- `panel_geo_position` — `[lat, lon, elev]` or `null`; comes from
  `schema.local_to_geo`, so a fault here lines up with the same coordinate a
  real SCADA feed would report for the same panel (§6.2).
- Round-trip with `FaultReport.from_dict(json.loads(msg.data))` — implemented
  and tested in `tests/test_fault_report.py`; `ros2_bridge.py` should reuse
  `to_dict`/`from_dict`, not hand-roll the JSON shape again.

## 4. Namespacing

- One namespace per robot id from `mission.yaml`'s `fleet:` block, e.g.
  `/ground_bot/...`, `/drone1/...`, `/drone2/...` (matching the ids used
  throughout `orchestrator/mission.py`'s `Fleet` and `transport.pose(robot_id)`
  / `transport.capture(robot_id)` calls — `ros2_bridge.py`'s `robot_id` params
  map 1:1 onto these namespaces).
- Prefer explicit per-robot namespaces over Isaac's auto-namespace feature —
  the bible flags it as flaky in deep hierarchies (§6.3).
- `/mission/fault` and `/clock` are global (no robot namespace).

## 5. QoS — the RViz2 gotcha

Isaac Sim's ROS 2 camera bridge publishes with **Sensor Data QoS (Best
Effort)**. If you subscribe with the default Reliable QoS (e.g. RViz2's
default), **you will see nothing and get no error** — set the image display's
Reliability to **Best Effort** explicitly. `cmd_vel`, `pose`, and
`/mission/fault` are Reliable (control/state messages, not high-rate sensor
streams — dropping one matters more than for a video frame).

## 6. Timing gotcha

ROS 2 OmniGraph nodes (`OgnROS2CameraHelper`, etc.) only publish **after you
press Play** in Isaac Sim. A headless run must still trigger the equivalent of
Play (`SimulationApp`/timeline start) before expecting any topic traffic —
this is a Track S concern in `world/sim_runtime.py`, noted here because a
`ros2_bridge.py` smoke test that "sees nothing" is often this, not a bridge bug.

## 7. What `ros2_bridge.py` must implement

Same `Transport` interface as `sim_native.py` (`transport/base.py`):

| `Transport` method | ROS 2 realization |
|---|---|
| `capture(robot_id)` | subscribe `/<robot_id>/camera/image_raw`, return the latest frame (block or return last-seen — decide once building; document the choice here) |
| `pose(robot_id)` | subscribe `/<robot_id>/pose`, return the latest `Pose` |
| `read_panel(panel_id)` | ⚠ open question — see §8 |
| `write_panel(...)` | publish a `FaultReport` (§3) on `/mission/fault`; USD write-back still happens on the sim side (bible §2.3, USD stays the source of truth) |
| `step(dt)` | no-op or a `/clock`-driven wait — the sim process owns real time here, unlike sim-native's single-process step |

## 8. Open question (flag before implementing)

`read_panel` needs the **current panel state**, which lives on the USD stage,
not on any topic in the table above. Options for Track S to decide when
building `ros2_bridge.py`:
1. Add a topic/service exposing panel state (e.g. `/mission/panel_state`
   request-reply), or
2. Keep `read_panel`/`write_panel` as a direct (non-ROS) side-channel into the
   sim process even when `Transport` is otherwise ROS 2 — acceptable since USD
   is the source of truth and panel reads are not a real-time control path.

Default recommendation (Track N, non-binding): **option 2** — it's simpler and
doesn't invent a new topic for something that isn't really sensor/actuator
data. Record the actual decision here once made.
