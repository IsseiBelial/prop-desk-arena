"""The four factions that compete in the Arena."""
from .ascendant import Ascendant
from .brood import Brood
from .legion import IronLegion
from .syndicate import Syndicate

FACTIONS = {"legion": IronLegion, "ascendant": Ascendant, "brood": Brood, "syndicate": Syndicate}
