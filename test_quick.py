"""Quick manual test — discovery + functional sanity checks for all modules."""

import sys
import importlib
import pkgutil
import inspect
from pathlib import Path

import numpy as np

# ── Bootstrap ─────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# ── 1. AUTO-DISCOVERY: importa todos os módulos em src/ ─────────────
print("=" * 70)
print("MODULE DISCOVERY (import smoke-test)")
print("=" * 70)

def import_all_modules(package_name: str = "src"):
    """Recursively import every .py module under `package_name`."""
    failures = []
    successes = []

    try:
        package = importlib.import_module(package_name)
    except ImportError as exc:
        print(f"❌ Cannot import root package '{package_name}': {exc}")
        return failures, successes

    for _, modname, ispkg in pkgutil.walk_packages(
        package.__path__, package.__name__ + "."
    ):
        try:
            importlib.import_module(modname)
            successes.append(modname)
            print(f"  ✅ {modname}")
        except Exception as exc:
            failures.append((modname, exc))
            print(f"  ❌ {modname}: {type(exc).__name__}: {exc}")

    return failures, successes

failures, successes = import_all_modules("src")

print(f"\n📊 Discovery results: {len(successes)} OK, {len(failures)} FAILED")
if failures:
    print("\n⚠️  Import failures:")
    for mod, exc in failures:
        print(f"   - {mod}: {exc}")

# ── 2. FUNCTIONAL SANITY TESTS ────────────────────────────────────────
print("\n" + "=" * 70)
print("FUNCTIONAL SANITY TESTS")
print("=" * 70)

# ── Shared imports ────────────────────────────────────────────────────
from src.core.constants import Region, RegionCRS
from src.spatial.grid_utils import get_analysis_grid
from src.spatial._config_helpers import get_grid_crs
from config.config_loader import load_scenario_config

config = load_scenario_config()

# ── Test A: Constants ─────────────────────────────────────────────────
print("\n--- Test A: Region / RegionCRS (equal-area AEA) ---")
assert Region.NORDESTE_BR.value == "brazil"
assert Region.NORTH_GERMANY.value == "germany"

br_crs = RegionCRS.projected_crs_for(Region.NORDESTE_BR)
de_crs = RegionCRS.projected_crs_for(Region.NORTH_GERMANY)

# CRS agora são strings PROJ4 de Albers Equal Area Conic, não EPSG fixos.
# Validamos a estrutura esperada em vez de um código EPSG hardcoded.
assert "+proj=aea" in br_crs, f"Expected AEA projection for Brazil, got: {br_crs}"
assert "+proj=aea" in de_crs, f"Expected AEA projection for Germany, got: {de_crs}"
assert "+lat_0=-10" in br_crs, f"Unexpected Brazil AEA center latitude: {br_crs}"
assert "+lat_0=53.75" in de_crs, f"Unexpected Germany AEA center latitude: {de_crs}"
assert br_crs != de_crs, "Brazil and Germany must not share the same custom CRS"

print(f"    Brazil CRS:  {br_crs}")
print(f"    Germany CRS: {de_crs}")
print("✅ Region enum and RegionCRS (AEA) mapping correct")

# ── Test B: Config loader ───────────────────────────────────────────
print("\n--- Test B: ScenarioConfig loaded from YAML ---")
assert hasattr(config, "regions")
assert hasattr(config, "electrolyzer")
assert hasattr(config, "renewables")
region_keys = list(type(config.regions).model_fields.keys())
print(f"    Regions loaded: {region_keys}")
print("✅ ScenarioConfig structure OK")

# ── Test B2: economic parameters match methodology (regression guard) ──
print("\n--- Test B2: Economic parameters vs methodology (regression guard) ---")
# Estes valores são âncoras diretas do texto da metodologia (seção 2.5).
# Se qualquer um destes falhar, o YAML divergiu da metodologia de novo --
# NÃO ajuste este teste para "passar"; corrija o YAML ou a metodologia.
alk = config.electrolyzer.technologies["alkaline"]
assert abs(alk.capex_usd_per_kw - 700.0) < 1e-6, (
    f"Alkaline CAPEX diverges from methodology (IRENA 2020, 700 USD/kW): "
    f"got {alk.capex_usd_per_kw}"
)
assert abs(alk.efficiency_kwh_per_kg - 51.0) < 1e-6, (
    f"Alkaline efficiency diverges from methodology (IRENA 2020, 51 kWh/kg): "
    f"got {alk.efficiency_kwh_per_kg}"
)

# power_density_w_per_m2 (flat 30 MW/km2 anchor) was migrated to
# config.technologies.solar_pv.<region>.capacity_density_mw_per_km2, a
# literature-validated {min, baseline, max} range (43-60, baseline 51.5)
# -- see docs/memory/10_capacity_density_assumptions.md. The old flat
# 30 MW/km2 anchor is intentionally superseded, not a regression: this was
# a deliberate methodology update, not a YAML/methodology divergence.
solar_density = config.technologies.solar_pv.brazil.capacity_density_mw_per_km2
assert abs(solar_density.baseline - 51.5) < 1e-6, (
    f"Solar capacity density baseline diverges from methodology "
    f"(51.5 MW/km2, NREL fixed-tilt): got {solar_density.baseline}"
)
assert abs(solar_density.min - 43.0) < 1e-6 and abs(solar_density.max - 60.0) < 1e-6, (
    f"Solar capacity density range diverges from methodology "
    f"(43-60 MW/km2): got [{solar_density.min}, {solar_density.max}]"
)

shared = config.electrolyzer.shared
implied_water_cost_per_kg = (
    shared.water_cost_usd_per_m3 * shared.water_consumption_l_per_kg / 1000.0
)
assert abs(implied_water_cost_per_kg - 0.05) < 1e-4, (
    f"Implied water cost diverges from methodology (0.05 USD/kg H2): "
    f"got {implied_water_cost_per_kg:.4f} USD/kg "
    f"(water_cost_usd_per_m3={shared.water_cost_usd_per_m3}, "
    f"water_consumption_l_per_kg={shared.water_consumption_l_per_kg})"
)
print(f"    Alkaline CAPEX: {alk.capex_usd_per_kw} USD/kW (methodology: 700.0)")
print(f"    Alkaline efficiency: {alk.efficiency_kwh_per_kg} kWh/kg (methodology: 51.0)")
print(f"    Solar capacity density baseline: {solar_density.baseline} MW/km2 (methodology: 51.5)")
print(f"    Implied water cost: {implied_water_cost_per_kg:.4f} USD/kg (methodology: 0.05)")
print("✅ Economic parameters match methodology")

# ⚠️ NÃO validado aqui (decisão pendente sua, não é bug de código):
#   - regions.germany.incentives.production_credit_usd_per_kg (ambiguidade EUR/USD)
#   - thresholds.* (slope/capacity_factor/buffer -- aguardando sua confirmação
#     de desenho antes de virar critério testável)

# ── Test C: _config_helpers ─────────────────────────────────────────
print("\n--- Test C: _config_helpers.get_grid_crs ---")
for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
    crs = get_grid_crs(region, config)
    expected = RegionCRS.projected_crs_for(region)
    assert crs == expected, (
        f"CRS mismatch for {region.value}: config has '{crs}', "
        f"RegionCRS expects '{expected}'. These must match EXACTLY "
        f"(string equality on PROJ4 definitions) -- check for whitespace "
        f"or parameter drift between scenario_params.yaml and constants.py."
    )
    print(f"    {region.value}: {crs}")
print("✅ get_grid_crs matches RegionCRS for all regions")

# ── Test D: grid_utils — explicit bounds (offline) ──────────────────
print("\n--- Test D: get_analysis_grid (explicit bounds, offline) ---")
# Bounds arbitrários em metros -- válidos independentemente da CRS escolhida,
# pois este teste só verifica a aritmética de grid (largura/altura/transform),
# não a geometria real da região.
bounds_br = (500000, 9500000, 800000, 9700000)
grid, transform = get_analysis_grid(Region.NORDESTE_BR, config, bounds=bounds_br)
assert grid.shape == (200, 300), f"Expected (200, 300), got {grid.shape}"
assert grid.dtype == np.float64
assert not np.isnan(transform.a)  # sanity: transform is populated
print(f"    Shape: {grid.shape}")
print("✅ Offline grid construction OK")

# ── Test E: grid_utils — dynamic bounds (requires network) ────────
print("\n--- Test E: get_analysis_grid (dynamic bounds, network) ---")
try:
    grid, transform = get_analysis_grid(Region.NORDESTE_BR, config)
    assert grid.shape[0] > 0 and grid.shape[1] > 0
    print(f"    BR shape: {grid.shape} (~{grid.shape[1]} x {grid.shape[0]} km)")
    print("✅ Dynamic BR grid OK")
except Exception as exc:
    print(f"    ⚠️  BR skipped/failed: {type(exc).__name__}: {exc}")

try:
    grid, transform = get_analysis_grid(Region.NORTH_GERMANY, config)
    assert grid.shape[0] > 0 and grid.shape[1] > 0
    print(f"    DE shape: {grid.shape} (~{grid.shape[1]} x {grid.shape[0]} km)")
    print("✅ Dynamic DE grid OK")
except Exception as exc:
    print(f"    ⚠️  DE skipped/failed: {type(exc).__name__}: {exc}")

# ── Test F: admin_boundaries — ACTUALLY CALL get_region_bounds ─────
# Este é o teste mais importante desta rodada: valida que a mudança de
# GADM nível 2 (5 distritos) para nível 1 (2 estados inteiros) realmente
# pegou a geometria certa, e que o sanity check de extensão em
# get_region_bounds() não dispara para uma região válida.
print("\n--- Test F: admin_boundaries.get_region_bounds (network) ---")
try:
    from src.spatial.admin_boundaries import get_region_bounds, AdminBoundaryError

    sig = inspect.signature(get_region_bounds)
    print(f"    get_region_bounds signature: {sig}")

    for region in (Region.NORDESTE_BR, Region.NORTH_GERMANY):
        try:
            minx, miny, maxx, maxy = get_region_bounds(region)
            width_km = (maxx - minx) / 1000.0
            height_km = (maxy - miny) / 1000.0
            print(
                f"    {region.value}: width={width_km:,.0f} km, "
                f"height={height_km:,.0f} km "
                f"(bounds: {minx:,.0f}, {miny:,.0f}, {maxx:,.0f}, {maxy:,.0f})"
            )
        except AdminBoundaryError as abe:
            print(f"    ❌ {region.value} FAILED sanity/fetch check: {abe}")
        except Exception as exc:
            print(f"    ⚠️  {region.value} skipped (network/other): {type(exc).__name__}: {exc}")

    print("✅ admin_boundaries.get_region_bounds executed (see widths/heights above)")
    print(
        "    Manually confirm: Brazil ~9 states (MA-BA) should be roughly "
        "1500-2000 km wide; Germany (SH+Niedersachsen, full states) should "
        "be roughly 250-350 km wide, NOT the ~5-district subset from before."
    )
except Exception as exc:
    print(f"    ❌ admin_boundaries failed: {exc}")

# ── Test G: spatial — data_layers ───────────────────────────────────
print("\n--- Test G: data_layers ---")
try:
    from src.spatial import data_layers
    members = [name for name in dir(data_layers) if not name.startswith("_")]
    print(f"    Exports: {members}")
    if "load_distance_to_water" not in members:
        print("    ⚠️  load_distance_to_water NOT yet exported (expected, per Stage 2 plan)")
    print("✅ data_layers import OK")
except Exception as exc:
    print(f"    ❌ data_layers failed: {exc}")

# ── Test H: spatial — exclusion_mask ──────────────────────────────
print("\n--- Test H: exclusion_mask ---")
try:
    from src.spatial import exclusion_mask
    members = [name for name in dir(exclusion_mask) if not name.startswith("_")]
    print(f"    Exports: {members}")
    print("✅ exclusion_mask import OK")
except Exception as exc:
    print(f"    ❌ exclusion_mask failed: {exc}")

# ── Test I: spatial — site_selection ──────────────────────────────
print("\n--- Test I: site_selection ---")
try:
    from src.spatial import site_selection
    members = [name for name in dir(site_selection) if not name.startswith("_")]
    print(f"    Exports: {members}")
    print("✅ site_selection import OK")
except Exception as exc:
    print(f"    ❌ site_selection failed: {exc}")

# ── Test J: spatial — topsis (sanity with dummy data) ───────────────
print("\n--- Test J: topsis ---")
try:
    from src.spatial import topsis
    func = getattr(topsis, "topsis", None) or getattr(topsis, "run_topsis", None)
    if func:
        sig = inspect.signature(func)
        print(f"    Main function: {func.__name__}{sig}")
        dummy_matrix = np.array([
            [2500, 3, 4],
            [2000, 5, 3],
            [3000, 2, 5]
        ], dtype=float)
        dummy_weights = np.array([0.5, 0.3, 0.2])
        try:
            result = func(dummy_matrix, dummy_weights)
            print(f"    Dummy result type: {type(result)}")
            print("✅ topsis functional sanity OK")
        except TypeError as te:
            print(f"    ⚠️  topsis dummy call failed (signature mismatch?): {te}")
            print("✅ topsis import OK (adjust dummy call to test fully)")
    else:
        print("    ⚠️  No 'topsis' or 'run_topsis' function found — inspect exports")
        print("✅ topsis import OK")
except Exception as exc:
    print(f"    ❌ topsis failed: {exc}")

# ── Test K: spatial — vikor (sanity with dummy data) ────────────────
print("\n--- Test K: vikor ---")
try:
    from src.spatial import vikor
    func = getattr(vikor, "vikor", None) or getattr(vikor, "run_vikor", None)
    if func:
        sig = inspect.signature(func)
        print(f"    Main function: {func.__name__}{sig}")
        dummy_matrix = np.array([
            [2500, 3, 4],
            [2000, 5, 3],
            [3000, 2, 5]
        ], dtype=float)
        dummy_weights = np.array([0.5, 0.3, 0.2])
        try:
            result = func(dummy_matrix, dummy_weights)
            print(f"    Dummy result type: {type(result)}")
            print("✅ vikor functional sanity OK")
        except TypeError as te:
            print(f"    ⚠️  vikor dummy call failed (signature mismatch?): {te}")
            print("✅ vikor import OK (adjust dummy call to test fully)")
    else:
        print("    ⚠️  No 'vikor' or 'run_vikor' function found — inspect exports")
        print("✅ vikor import OK")
except Exception as exc:
    print(f"    ❌ vikor failed: {exc}")

# ── Test L: economics — lcoh_model ──────────────────────────────────
print("\n--- Test L: lcoh_model ---")
try:
    from src.economics import lcoh_model
    members = [name for name in dir(lcoh_model) if not name.startswith("_")]
    print(f"    Exports: {members}")
    candidates = [m for m in members if "lcoh" in m.lower()]
    if candidates:
        for c in candidates:
            obj = getattr(lcoh_model, c)
            if callable(obj):
                print(f"    Callable: {c}{inspect.signature(obj)}")
    print("✅ lcoh_model import OK")
except Exception as exc:
    print(f"    ❌ lcoh_model failed: {exc}")

# ── Test M: economics — decomposition ───────────────────────────────
print("\n--- Test M: decomposition ---")
try:
    from src.economics import decomposition
    members = [name for name in dir(decomposition) if not name.startswith("_")]
    print(f"    Exports: {members}")
    print("✅ decomposition import OK")
except Exception as exc:
    print(f"    ❌ decomposition failed: {exc}")

# ── Test N: potential — h2_potential ────────────────────────────────
print("\n--- Test N: h2_potential ---")
try:
    from src.potential import h2_potential
    members = [name for name in dir(h2_potential) if not name.startswith("_")]
    print(f"    Exports: {members}")
    print("✅ h2_potential import OK")
except Exception as exc:
    print(f"    ❌ h2_potential failed: {exc}")

# ── Test O: sensitivity — sensitivity_analysis ──────────────────────
print("\n--- Test O: sensitivity_analysis ---")
try:
    from src.sensitivity import sensitivity_analysis
    members = [name for name in dir(sensitivity_analysis) if not name.startswith("_")]
    print(f"    Exports: {members}")
    print("✅ sensitivity_analysis import OK")
except Exception as exc:
    print(f"    ❌ sensitivity_analysis failed: {exc}")

# ── Test P: viz — plotting ──────────────────────────────────────────
print("\n--- Test P: plotting ---")
try:
    from src.viz import plotting
    members = [name for name in dir(plotting) if not name.startswith("_")]
    print(f"    Exports: {members}")
    print("✅ plotting import OK")
except Exception as exc:
    print(f"    ❌ plotting failed: {exc}")

# ── Test Q: Rodar testes unitários existentes (pytest) ──────────────
print("\n--- Test Q: Existing unit tests in tests/ ---")
try:
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    print(result.stdout)
    if result.returncode != 0:
        print("⚠️  Some pytest tests failed (see output above)")
    else:
        print("✅ All pytest tests passed")
except FileNotFoundError:
    print("    ⚠️  pytest not installed — skipping (pip install pytest to enable)")
except Exception as exc:
    print(f"    ⚠️  Could not run pytest: {exc}")

# ── Summary ─────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Modules imported OK   : {len(successes)}")
print(f"Modules failed        : {len(failures)}")
print(f"Functional tests      : see above ✅/❌/⚠️")
print("=" * 70)