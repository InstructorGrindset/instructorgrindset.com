"""Write a BeamNG level folder and pack it as a mod zip.

The layout follows a level saved by the BeamNG World Editor (BeamNG's Utah, as shipped in the open source Utah2
edit at https://github.com/MattB70/BeamNG-Utah2)::

    levels/<name>/info.json                         title, previews, spawn points
    levels/<name>/main/items.level.json             MissionGroup
    levels/<name>/main/MissionGroup/...             one items.level.json per SimGroup, one JSON object per line
    levels/<name>/theTerrain.ter, .terrain.json     the terrain (see ``ter``)
    levels/<name>/art/terrains/main.materials.json  terrain materials and their texture set

A mod zip holds the ``levels`` folder at its root; BeamNG mounts every zip in its ``mods`` folder.
"""
from __future__ import annotations

import json
import math
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from tools.beamng import paint, ter, textures
from tools.beamng.geo import Grid

TERRAIN_STEM = "theTerrain"
TEXTURE_SET = "RealWorldTerrainTextureSet"

# Per layer index: (look in textures.LOOKS, BeamNG ground model). The ground models are BeamNG's own (the names its
# Utah level uses), so tyres grip like on that surface.
MATERIALS = [
    ("grass", "GRASS"),
    ("rock", "ROCK"),
    ("ski_run", "DIRT_GRASS"),
    ("dirt", "GRAVEL"),
    ("asphalt", "ASPHALT"),
]
assert [paint.GRASS, paint.ROCK, paint.SKI_RUN, paint.DIRT, paint.ASPHALT] == list(range(len(MATERIALS)))


@dataclass(frozen=True)
class Spawn:
    name: str
    x: float
    y: float
    z: float
    heading: tuple[float, float] = (0.0, 1.0)   # unit vector the vehicle faces (world x, y)
    title: Optional[str] = None


def pid(level: str, key: str) -> str:
    """A persistentId that stays the same across builds of the same level."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"instructorgrindset.com/beamng/{level}/{key}"))


def material_names(level: str) -> list[str]:
    return [f"{level}_{look}" for look, _ in MATERIALS]


def rotation(heading: tuple[float, float]) -> list[float]:
    """rotationMatrix (row major) turning a vehicle, whose front is local -y, to face ``heading``."""
    dx, dy = heading
    a = math.atan2(dx, -dy)
    c, s = math.cos(a), math.sin(a)
    return [round(v, 6) for v in (c, -s, 0, s, c, 0, 0, 0, 1)]


def materials_json(level: str, extent_m: float, files: dict[str, str]) -> dict:
    root = f"/levels/{level}/art/terrains/"
    out = {TEXTURE_SET: {
        "name": TEXTURE_SET, "class": "TerrainMaterialTextureSet", "persistentId": pid(level, TEXTURE_SET),
        "baseTexSize": [textures.BASE_PX, textures.BASE_PX],
        "detailTexSize": [textures.DETAIL_PX, textures.DETAIL_PX],
        "macroTexSize": [textures.MACRO_PX, textures.MACRO_PX],
    }}
    base = round(extent_m)
    for (look, ground), internal in zip(MATERIALS, material_names(level)):
        p = pid(level, internal)
        t = lambda key: root + files[key]  # noqa: E731
        out[f"{internal}-{p}"] = {
            "name": f"{internal}-{p}", "internalName": internal, "class": "TerrainMaterial", "persistentId": p,
            "groundmodelName": ground,
            "baseColorBaseTex": t(f"{look}_base_b"), "baseColorBaseTexSize": base,
            "baseColorDetailTex": t(f"{look}_b"), "baseColorDetailTexSize": textures.DETAIL_M,
            "baseColorDetailStrength": [0.35, 0.35],
            "baseColorMacroTex": t("macro_b"), "baseColorMacroTexSize": textures.MACRO_M,
            "baseColorMacroStrength": [0.1, 0.15],
            "normalBaseTex": t("base_nm"), "normalBaseTexSize": base,
            "normalDetailTex": t(f"{look}_nm"), "normalDetailTexSize": textures.DETAIL_M,
            "normalDetailStrength": [0.6, 0.2],
            "normalMacroTex": t("macro_nm"), "normalMacroTexSize": textures.MACRO_M,
            "normalMacroStrength": [0.4, 0.4],
            "roughnessBaseTex": t("base_r"), "roughnessBaseTexSize": base,
            "roughnessDetailTex": t(f"{look}_r"), "roughnessDetailTexSize": textures.DETAIL_M,
            "roughnessDetailStrength": [0.3, 0.3],
            "roughnessMacroTex": t("macro_r"), "roughnessMacroTexSize": textures.MACRO_M,
            "roughnessMacroStrength": [0.15, 0.5],
            "aoBaseTex": t("base_ao"), "aoBaseTexSize": base,
            "aoDetailTex": t(f"{look}_ao"), "aoDetailTexSize": textures.DETAIL_M,
            "aoMacroTex": t("macro_ao"), "aoMacroTexSize": textures.MACRO_M,
            "heightBaseTex": t("base_h"), "heightBaseTexSize": base,
            "heightDetailTex": t(f"{look}_h"), "heightDetailTexSize": textures.DETAIL_M,
            "heightMacroTex": t("macro_h"), "heightMacroTexSize": textures.MACRO_M,
            "detailDistances": [0, 0, 50, 100], "detailDistAtten": [1, 1],
            "macroDistances": [0, 10, 100, 3000], "macroDistAtten": [0, 1],
        }
    return out


def scene(level: str, title: str, grid: Grid, base_z: float, max_height: float,
          spawns: Sequence[Spawn]) -> dict[str, list[dict]]:
    """{items.level.json path inside main/: objects}."""
    def group(name: str, parent: Optional[str]) -> dict:
        obj = {"name": name, "class": "SimGroup", "persistentId": pid(level, name)}
        if parent:
            obj["__parent"] = parent
        return obj

    view = min(8000, max(2000, round(grid.extent_m)))
    objects = [
        {"name": "theLevelInfo", "class": "LevelInfo", "persistentId": pid(level, "theLevelInfo"),
         "__parent": "Level_objects", "canvasClearColor": [253, 253, 253, 255], "desc0": title,
         "fogAtmosphereHeight": 1000, "fogColor": [0.57, 0.77, 1, 1], "fogDensity": 0.0002,
         "fogDensityOffset": 1, "gravity": -9.81, "levelName": title, "visibleDistance": view},
        {"name": "sunsky", "class": "ScatterSky", "persistentId": pid(level, "sunsky"), "__parent": "Level_objects",
         "position": [0, 0, base_z + 100], "ambientScale": [1, 0.894, 0.780, 1],
         "ambientScaleGradientFile": "art/sky_gradients/default/gradient_ambient.png", "azimuth": 245.8,
         "brightness": 0.9, "colorize": [0.216, 0.349, 0.604, 1],
         "colorizeGradientFile": "art/sky_gradients/default/gradient_colorize.png", "elevation": 33.5,
         "exposure": 1.4, "fogScale": [0.396, 0.667, 1, 1],
         "fogScaleGradientFile": "art/sky_gradients/default/gradient_fog.png", "mieScattering": 0.0004,
         "nightColor": [1, 0.894, 0.780, 1], "nightFogColor": [0.396, 0.667, 1, 1],
         "nightFogGradientFile": "art/sky_gradients/default/gradient_fog.png",
         "nightGradientFile": "art/sky_gradients/default/gradient_ambient.png", "shadowDistance": 1600,
         "shadowSoftness": 0.2, "skyBrightness": 42, "sunScale": [0.996, 0.831, 0.729, 1],
         "sunScaleGradientFile": "art/sky_gradients/default/gradient_sunscale.png", "texSize": 1024},
        {"name": "tod", "class": "TimeOfDay", "persistentId": pid(level, "tod"), "__parent": "Level_objects",
         "position": [0, 0, base_z + 100], "axisTilt": -20, "azimuthOverride": 0, "play": False,
         "startTime": 0.15, "time": 0.15},
        {"name": TERRAIN_STEM, "class": "TerrainBlock", "persistentId": pid(level, TERRAIN_STEM),
         "__parent": "Level_objects", "position": [grid.origin, grid.origin, round(base_z, 4)],
         "baseTexSize": 2048, "materialTextureSet": TEXTURE_SET, "maxHeight": round(max_height, 4),
         "squareSize": grid.square_m, "terrainFile": f"/levels/{level}/{TERRAIN_STEM}.ter"},
    ]
    drops = [{"name": s.name, "class": "SpawnSphere", "persistentId": pid(level, s.name),
              "__parent": "PlayerDropPoints", "position": [round(s.x, 3), round(s.y, 3), round(s.z, 3)],
              "autoplaceOnSpawn": "0", "dataBlock": "SpawnSphereMarker", "homingCount": "0", "indoorWeight": "1",
              "lockCount": "0", "outdoorWeight": "1", "radius": 5, "rotationMatrix": rotation(s.heading),
              "sphereWeight": "1"} for s in spawns]
    return {
        "items.level.json": [group("MissionGroup", None)],
        "MissionGroup/items.level.json": [group("Level_objects", "MissionGroup"),
                                          group("PlayerDropPoints", "MissionGroup")],
        "MissionGroup/Level_objects/items.level.json": objects,
        "MissionGroup/PlayerDropPoints/items.level.json": drops,
    }


def info_json(level: str, title: str, description: str, grid: Grid, spawns: Sequence[Spawn],
              authors: str) -> dict:
    return {
        "title": title,
        "description": description,
        "authors": authors,
        "previews": ["preview.png"],
        "size": [round(grid.extent_m), round(grid.extent_m)],
        "defaultSpawnPointName": spawns[0].name,
        "spawnPoints": [{"objectname": s.name, "translationId": s.title or s.name, "preview": "preview.png"}
                        for s in spawns],
        "supportsTraffic": False,
        "supportsTimeOfDay": True,
    }


def heightmap_png(q: np.ndarray) -> bytes:
    """The quantized heights as a north up 16 bit greyscale PNG, for the World Editor's terrain import."""
    return textures.encode_png(np.asarray(q[::-1], dtype=np.uint16))


def preview(grid: Grid, h: np.ndarray, layer: np.ndarray, px: int = 512) -> bytes:
    """North up picture of the level: material colors under a hillshade lit from the north west."""
    step = max(1, grid.size // px)
    hs, ls = h[::step, ::step], layer[::step, ::step]
    gy, gx = np.gradient(hs, grid.square_m * step)
    light = np.array([-1.0, 1.0, 1.0]) / math.sqrt(3)  # from the north west, 45 degrees up
    n = np.stack([-gx, -gy, np.ones_like(hs)], axis=-1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    shade = np.clip(n @ light, 0, 1)[..., None]
    colors = np.array([np.add(textures.LOOKS[look].dark, textures.LOOKS[look].light) / 2 for look, _ in MATERIALS])
    rgb = colors[ls] / 255 * (0.35 + 0.85 * shade)
    return textures.to_png(rgb[::-1])  # row 0 of the grid is the south edge


def write_level(root: Path, level: str, title: str, description: str, grid: Grid, h: np.ndarray,
                layer: np.ndarray, spawns: Sequence[Spawn], provenance: dict, authors: str,
                heightmap: Optional[Path] = None) -> Path:
    """Write ``root/levels/<level>/`` and return that folder; ``heightmap`` also saves the heights as a PNG
    there (outside the level, which reads only the .ter)."""
    folder = root / "levels" / level
    q, base_z, max_height = ter.quantize(h)
    names = material_names(level)
    ter.write(folder, TERRAIN_STEM, f"/levels/{level}", q, layer, names)
    if heightmap:
        heightmap.parent.mkdir(parents=True, exist_ok=True)
        heightmap.write_bytes(heightmap_png(q))
    files = textures.write_all(folder / "art" / "terrains", [look for look, _ in MATERIALS])
    (folder / "art" / "terrains" / "main.materials.json").write_text(
        json.dumps(materials_json(level, grid.extent_m, files), indent=2) + "\n", encoding="utf-8")
    for rel, objs in scene(level, title, grid, base_z, max_height, spawns).items():
        path = folder / "main" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(o, separators=(",", ":")) + "\n" for o in objs), encoding="utf-8")
    (folder / "info.json").write_text(json.dumps(info_json(level, title, description, grid, spawns, authors),
                                                 indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (folder / "preview.png").write_bytes(preview(grid, h, layer))
    meta = dict(provenance, terrain={"size": grid.size, "square_m": grid.square_m, "extent_m": grid.extent_m,
                                     "base_z_m": base_z, "max_height_m": max_height,
                                     "height_step_m": max_height / 65535, "materials": names})
    (folder / "source.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return folder


def pack(root: Path, zip_path: Path) -> Path:
    """Zip ``root/levels`` into ``zip_path`` with ``levels/`` at the zip root."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted((root / "levels").rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(root).as_posix())
    return zip_path
