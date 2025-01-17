"""All possible explore types."""
from .explore import Explore  # noqa: F401
from .growth_accounting_explore import GrowthAccountingExplore
from .ping_explore import PingExplore

explore_types = {
    PingExplore.type: PingExplore,
    GrowthAccountingExplore.type: GrowthAccountingExplore,
}
