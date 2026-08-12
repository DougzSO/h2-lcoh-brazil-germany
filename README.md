# Hydrogen LCOH Decomposition Pipeline — Northeast Brazil vs. North Germany

A reproducible geospatial-economic research pipeline comparing the
Levelised Cost of Hydrogen (LCOH) of green hydrogen production between
Northeast Brazil and North Germany (Schleswig-Holstein and Niedersachsen).

## Academic summary

The study asks to what extent superior renewable resource endowment can
compensate for higher cost of capital in green hydrogen production, and
under what conditions that compensation breaks down. Rather than ranking
the two countries directly, the design isolates two effects the
literature typically treats separately — physical resource quality and
cost of capital — and recombines them in a controlled counterfactual
framework to identify an **inversion point**: the financing-cost threshold
at which the ranking between a resource-rich, capital-constrained region
(Northeast Brazil) and a resource-modest, capital-abundant region (North
Germany) reverses.

Northeast Brazil and North Germany were selected as a paired case because
they represent a near-maximal contrast in resource endowment (Global
Horizontal Irradiance ≈1,800–2,200 kWh/m²/yr vs. ≈950–1,100 kWh/m²/yr)
combined with a near-inverse contrast in financing conditions (weighted
average cost of capital markedly higher in Brazil than in Germany for
equivalent renewable asset classes) — the necessary condition for an
inversion point to exist at all.

The full academic methodology, including all cited parameter sources and
stated limitations, is in [`METHODOLOGY.md`](METHODOLOGY.md); the complete
software architecture is in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Key research stages

The codebase mirrors the four analytical stages of the methodology one to
one:

1. **Spatial suitability assessment (MCDA)** — TOPSIS (primary) and VIKOR
   (robustness cross-check) multi-criteria site suitability on a common
   1 km² equal-area grid, with a binary exclusion mask for protected
   areas, water bodies, and urban land.
2. **Technical potential** — installable capacity, annual energy yield,
   and annual hydrogen output for each retained candidate site.
3. **LCOH decomposition** — discounted lifetime LCOH under actual,
   WACC-swap counterfactual, and Brent's-method inversion-point financing
   conditions, plus REHIDRO (Brazil) and IPCEI Hy2Use-consistent (Germany)
   incentive scenarios.
4. **Sensitivity analysis** — one-at-a-time economic sensitivity sweep
   (WACC, CAPEX, electrolyzer specific energy consumption, country CAPEX
   multiplier, electrolyzer technology) and TOPSIS weight-perturbation
   robustness check against the VIKOR cross-check.

Each stage writes its output to disk (GeoTIFF, CSV, or JSON) before the
next stage reads it — every stage is independently runnable and
inspectable. See
[`docs/memory/02_architecture_and_dataflow.md`](docs/memory/02_architecture_and_dataflow.md)
for the full data-flow diagram.

## Repository structure

```
project-root/
├── README.md
├── CLAUDE.md                  # Agent/contributor operating rules
├── ARCHITECTURE.md            # Full software architecture
├── METHODOLOGY.md             # Full academic methodology
├── SPRINT_LOG.md              # Current development status, module-by-module
├── LICENSE
├── requirements.txt
├── run_pipeline.py            # CLI orchestrator (no analytical logic)
├── test_quick.py              # Import + functional smoke test
├── docs/
│   └── memory/                 # Persistent, modular reference documentation
│       ├── README.md
│       ├── 01_project_overview.md
│       ├── 02_architecture_and_dataflow.md
│       ├── 03_data_sources_and_acquisition.md
│       ├── 04_spatial_methodology.md
│       ├── 05_economic_model.md
│       ├── 06_technical_decisions_log.md
│       ├── 07_risks_and_limitations.md
│       └── 08_commands_and_reproducibility.md
├── config/
│   ├── scenario_params.yaml    # Every numeric parameter used in the study
│   └── config_loader.py        # Pydantic validation, single load point
├── src/
│   ├── core/                   # Region enum, RegionCRS (CRS source of truth)
│   ├── acquisition/             # ALL network I/O — no other layer touches the network
│   ├── spatial/                 # Grid, boundaries, layers, exclusion, TOPSIS, VIKOR, site selection
│   ├── potential/                # Technical hydrogen potential
│   ├── economics/                # LCOH model, decomposition, incentive scenarios
│   ├── sensitivity/               # OAT sensitivity + TOPSIS weight perturbation
│   └── viz/                       # Plotting
├── data/
│   ├── raw/                    # Written by acquisition/ ONLY — never by hand
│   ├── processed/               # Reprojected/aligned intermediates
│   └── boundaries/               # Dissolved administrative boundaries per region
├── outputs/
│   ├── maps/
│   ├── tables/
│   └── figures/
└── tests/
```

Module-by-module implementation status (complete / partial / not yet
implemented) is tracked in [`SPRINT_LOG.md`](SPRINT_LOG.md) and summarized
in
[`docs/memory/02_architecture_and_dataflow.md`](docs/memory/02_architecture_and_dataflow.md).

## Prerequisites and environment setup

- **Python 3.11**
- Core stack: `rasterio`, `geopandas`, `rasterstats`, `numpy`, `scipy`,
  `pydantic`, `matplotlib`, `pandas`, `requests`, `python-dotenv`,
  `pytest`, `pyyaml`

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> `requirements.txt` is currently unpopulated in this repository snapshot.
> Until it is filled in with pinned versions, install the packages listed
> above manually. Flag any dependency you add that is not yet reflected
> there, per `CLAUDE.md`.

### Credentials

Corine Land Cover (Germany land-use layer) requires a one-time manual
credential: a Copernicus Data Space Ecosystem (CDSE) token. Create an
untracked `.env` file at the project root:

```
CDSE_TOKEN=your_token_here
```

`src/acquisition/credentials.py` fails explicitly with a descriptive error
if a required credential is missing — it never proceeds silently. No other
data source in this pipeline requires manual credential setup or manual
recurring downloads; see
[`docs/memory/03_data_sources_and_acquisition.md`](docs/memory/03_data_sources_and_acquisition.md).

## Quickstart

```bash
# Smoke-test: import every module under src/ and run functional sanity checks
python test_quick.py

# Full pytest suite
pytest tests/

# Run currently implemented pipeline stages (config -> boundaries -> grid)
python run_pipeline.py --stage all

# Run a single stage
python run_pipeline.py --stage boundaries

# Restrict to one region
python run_pipeline.py --stage boundaries --region brazil
python run_pipeline.py --stage boundaries --region germany

# Run an analytical module directly via its own __main__ entry point
python -m src.economics.decomposition
python -m src.sensitivity.sensitivity_analysis
```

Requesting a pipeline stage that is not yet implemented fails loudly with
a clear message rather than silently skipping it. See
[`docs/memory/08_commands_and_reproducibility.md`](docs/memory/08_commands_and_reproducibility.md)
for the full stage-by-stage verification command reference.

## Data sources and citation acknowledgments

| Layer | Source |
|---|---|
| Solar irradiance (GHI, PVOUT) | Global Solar Atlas, World Bank / ESMAP (CC BY 4.0) |
| Wind resource (100 m speed, power density) | Global Wind Atlas |
| Terrain slope | SRTM 30 m, via OpenTopography / AWS Terrain Tiles |
| Administrative boundaries, Brazil | IBGE (Instituto Brasileiro de Geografia e Estatística) |
| Administrative boundaries, Germany | GADM |
| Land use, Brazil | MapBiomas, Collection 9 (2024) |
| Land use, Germany | Corine Land Cover, Copernicus Land Monitoring Service |
| Protected areas, Brazil | ICMBio / MMA |
| Protected areas, Germany | Natura 2000, European Environment Agency |
| Power infrastructure, Brazil | ANEEL SIGA |
| Power infrastructure, Germany | Marktstammdatenregister |
| Surface water bodies | OpenStreetMap waterway layer, via the Overpass API |

Key techno-economic parameter sources: IRENA (2020) for electrolyzer CAPEX
and efficiency baselines; IEA (2023) for post-2021 supply-chain cost
inflation; Glenk and Reichelstein (2019) for PEM specific energy
consumption cross-validation; Steffen (2020) for technology-differentiated
WACC evidence. Full citations and parameter derivations are in
[`METHODOLOGY.md`](METHODOLOGY.md) §2.2 and §2.5.

Two economic parameters are explicitly flagged as unconfirmed placeholders
pending primary-source verification — the REHIDRO (Brazil) and IPCEI
Hy2Use (Germany) incentive values — see
[`docs/memory/05_economic_model.md`](docs/memory/05_economic_model.md).
Do not cite results derived from these two values as final without
confirming the underlying figures.

## License and research context

This repository supports doctoral research comparing green hydrogen
production economics between Northeast Brazil and North Germany. See
[`LICENSE`](LICENSE) for licensing terms.
