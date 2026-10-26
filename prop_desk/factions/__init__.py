"""The five factions that compete in the Arena."""
from .ascendant import Ascendant
from .brood import Brood
from .kronos import KronosWatch
from .legion import IronLegion
from .syndicate import Syndicate

FACTIONS = {"legion": IronLegion, "ascendant": Ascendant, "brood": Brood, "syndicate": Syndicate,
            "kronos": KronosWatch}
