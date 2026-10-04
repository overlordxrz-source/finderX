"""Engine registry. Each engine is ``run(ctx: JobContext) -> dict``."""

from . import galaxy, solar, stellar, transit

ENGINES = {
    "transit": transit.run,
    "stellar": stellar.run,
    "galaxy": galaxy.run,
    "solar": solar.run,
}

STAGES = {
    "transit": transit.STAGES,
    "stellar": stellar.STAGES,
    "galaxy": galaxy.STAGES,
    "solar": solar.STAGES,
}
