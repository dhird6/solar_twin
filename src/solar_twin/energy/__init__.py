"""Energy: what the plant would PRODUCE, and what a fault costs (Isaac-free).

The twin could say a panel was soiled and could not say what that was worth. This
package closes that: real geometry + real sun -> plane-of-array irradiance -> cell
temperature -> DC -> AC, and a fault's cost in kWh and money.

⚠ Everything here is a **model**, not a measurement. We hold no SCADA feed for
Khavda, so nothing in this package has been validated against the real plant. See
`model.py`'s header for the four assumptions that bound it.
"""

from solar_twin.energy.model import (  # noqa: F401
    DERATE_BY_STATE,
    ModuleSpec,
    PlantSpec,
    PowerPoint,
    ProductionLoss,
    UNVALIDATED_CAVEAT,
    fault_cost,
    instant_power,
    plant_from_layout_cfg,
)
