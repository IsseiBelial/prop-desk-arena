"""The three factions that compete in the Arena."""
from .ascendant import Ascendant
from .brood import Brood
from .legion import IronLegion

FACTIONS = {"legion": IronLegion, "ascendant": Ascendant, "brood": Brood}
