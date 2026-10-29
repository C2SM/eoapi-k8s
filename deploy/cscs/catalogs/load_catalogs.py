#!/usr/bin/env python3
"""Generate the SQL that loads catalogs.yaml into pgSTAC.

Reads the collection list (JSON array of {"id", "title"}) on stdin, assigns
every collection to a leaf catalog with the rules below, and writes one
transaction to stdout that:

  - upserts each catalog in catalogs.yaml as a `type: Catalog` row,
  - deletes Catalog rows that are no longer in catalogs.yaml,
  - sets `parent_ids` on every collection to its leaf catalog.

Fails without output if a collection matches no rule. The catalog tree with
collection counts goes to stderr. See README.md for the full pipeline.
"""

import json
import re
import sys
from pathlib import Path

import yaml

CMR_ID = re.compile(r"^C\d+-(?P<provider>[A-Z_]+)$")


def has(title, *words):
    """True if any of `words` occurs in `title` as a whole word, ignoring case."""
    return any(
        re.search(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", title, re.I)
        for w in words
    )


def first_match(title, rules):
    for leaf, words in rules:
        if has(title, *words):
            return leaf
    return None


# MODIS and VIIRS share product families; BRDF/Albedo is checked first because
# its titles also mention reflectance.
FAMILY_RULES = [
    ("land-surface-temperature", ["Land Surface Temperature", "Thermal Bands", "Emissivity"]),
    ("fire", ["Fire", "Active Fires", "Burned Area", "Thermal Anomalies"]),
    ("vegetation", ["Vegetation Indices", "Vegetation Index", "Leaf Area Index", "Phenology",
                    "Land Cover Dynamics", "Vegetation Continuous Fields"]),
    ("productivity-et", ["Gross Primary Productivity", "Net Primary Production",
                         "Gross and Net Primary Production", "Evapotranspiration",
                         "Net Photosynthesis", "Photosynthetically Active Radiation"]),
    ("surface-reflectance", ["Surface Reflectance", "Land Surface BRF", "Ocean Reflectance", "MAIAC"]),
    ("radiation-aerosol", ["Downward", "Surface Radiation", "Aerosol"]),
    ("land-cover-water", ["Land Cover", "Water Mask", "Water Reservoir"]),
    ("geolocation", ["Geolocation"]),
]


def modis_viirs_family(title):
    if has(title, "BRDF", "Albedo", "NBAR"):
        if has(title, "QA", "Quality", "Valid Observation", "Snow Status", "SnowStatus",
               "Uncertainty", "Local Solar Noon", "LocalSolarNoon"):
            return "brdf-albedo-quality"
        if has(title, "Parameter", "Parameter1", "Parameter2", "Parameter3", "Model Parameters"):
            return "brdf-albedo-model-parameters"
        if has(title, "NBAR", "Nadir BRDF-Adjusted"):
            return "brdf-albedo-nbar"
        return "brdf-albedo-albedo"
    return first_match(title, FAMILY_RULES)


def viirs_satellite(title):
    # "VIIRS/NPP+JPSS1 ..." is filed with NPP.
    platform = title.split()[0].split("/")[1]
    return "npp" if platform.startswith("NPP") else platform.lower()


LP_OTHER_RULES = [
    ("ecostress", ["ECOSTRESS"]),
    ("emit", ["EMIT"]),
    ("gedi", ["GEDI"]),
    ("g-liht", ["G-LiHT"]),
    ("aster-camel", ["ASTER", "CAMEL"]),
    ("elevation", ["Shuttle Radar Topography", "NASADEM"]),
    ("hls-opera-dist", ["HLS", "OPERA"]),
    ("cropland", ["GFSAD", "Cropland", "Hyperspectral Imaging Spectral-library"]),
    ("landsat-derived", ["Web-Enabled Landsat", "Forest Cover Change", "Land Cover Mapping", "MuSLI"]),
    ("vegetation-climate-records", ["VIP", "Vegetation Continuous Fields"]),
    ("lpj-models", ["LPJ-EOSIM", "LPJ-PROSAIL"]),
    ("lst-climatologies", ["Earth Orbit Land Surface Temperature"]),
]

# Named campaigns before the generic GPM GV fallback, so that a
# "GPM GROUND VALIDATION ... IPHEx" title lands in IPHEx.
GHRC_RULES = [
    ("campaign-iphex", ["IPHEx"]),
    ("campaign-gcpex", ["GCPEX"]),
    ("campaign-olympex", ["OLYMPEX"]),
    ("campaign-mc3e", ["MC3E"]),
    ("campaign-ifloods", ["IFloodS"]),
    ("campaign-lpvex", ["LPVEx"]),
    ("campaign-hymex", ["HyMeX"]),
    ("campaign-impacts", ["IMPACTS"]),
    ("campaign-camex", ["CAMEX", "CAMEX-1", "CAMEX-2", "CAMEX-3", "CAMEX-4"]),
    ("campaign-cpex", ["CPEX", "CPEX-AW", "CPEX-CV"]),
    ("campaign-grip", ["GRIP"]),
    ("campaign-namma", ["NAMMA"]),
    ("campaign-tcsp", ["TCSP"]),
    ("campaign-hs3", ["HS3"]),
    ("campaign-tc4", ["TC4"]),
    ("campaign-epoch", ["EPOCH"]),
    ("campaign-lightning", ["GOES-R PLT", "ALOFT", "ACES", "RELAMPAGO"]),
    ("campaign-whymsie", ["WHyMSIE", "WH2yMSIE"]),
    ("campaign-other", ["CRYSTAL-FACE", "KWAJEX", "TRMM-LBA", "LBA", "TRMM Brazil", "TOGA",
                        "HOPEX", "FIRE III ACE", "CAPE EXPERIMENT", "JACKSONVILLE", "HIRAD"]),
    ("campaign-gpm-gv-other", ["GPM GROUND VALIDATION", "GPM GV", "SMAS"]),
    # SPoRT's Land Information System, not the Lightning Imaging Sensor.
    ("atmosphere-regional", ["Land Information System"]),
    ("lightning-glm", ["GLM"]),
    ("lightning-space", ["LIS", "OTD", "OLS"]),
    ("lightning-ground-networks", ["Lightning", "LMA", "LDAR", "WWLLN", "Field Mill", "Thunder"]),
    ("passive-microwave", ["SSM/I", "SSMIS", "AMSU", "TMI", "RSS", "SeaFlux", "Hail", "TCPF",
                           "AMSR", "MSU"]),
]

POCLOUD_RULES = [
    ("pre-swot-models", ["Pre-SWOT"]),
    ("swot", ["SWOT"]),
    ("ghrsst", ["GHRSST"]),
    ("smap-salinity", ["SMAP"]),
    ("opera-dswx", ["OPERA"]),
]


def leaf_for(collection):
    """Leaf catalog id for a collection, or None if no rule matches."""
    cid = collection["id"]
    title = " ".join(collection["title"].split())
    if cid.startswith("dyamond-"):
        return "dyamond"
    if cid == "noaa-emergency-response":
        return "aerial-imagery"
    m = CMR_ID.match(cid)
    provider = m["provider"] if m else None
    if provider == "LPCLOUD":
        if title.startswith(("MODIS", "VIIRS")):
            sensor = title[:5].lower()
            family = modis_viirs_family(title)
            if family is None:
                return None
            leaf = f"{sensor}-{family}"
            if sensor == "viirs" and family in ("brdf-albedo-albedo", "brdf-albedo-model-parameters"):
                leaf += "-" + viirs_satellite(title)
            return leaf
        return first_match(title, LP_OTHER_RULES)
    if provider == "GHRC_DAAC":
        return first_match(title, GHRC_RULES) or "atmosphere-regional"
    if provider == "POCLOUD":
        return first_match(title, POCLOUD_RULES)
    if provider == "ASF":
        return "nisar" if title.startswith("NISAR") else "opera-sentinel-1"
    if provider == "LARC_CLOUD":
        return "prefire" if "PREFIRE" in title else "sage-iii" if "SAGE III" in title else None
    if provider == "GES_DISC" and "NLDAS" in title:
        return "nldas"
    if provider == "NSIDC_CPRD":
        return "passive-microwave"
    return None


def walk(nodes, parent=None):
    for node in nodes:
        yield node, parent
        yield from walk(node.get("children", []), node["id"])


def catalog_document(node, parent, count):
    links = []
    if preview := node.get("preview"):
        links.append({"rel": "preview", **preview})
    description = node["description"].strip()
    # Every catalog but the root, whose description says it at length. First,
    # so that the card's description, cut at 300 characters, keeps it.
    if parent:
        note = (
            "**1 collection**; whether you see it depends on your login."
            if count == 1
            else f"**{count:,} collections** in total; how many of them you see"
            " depends on your login."
        )
        description = f"{note}\n\n{description}"
    return {
        "type": "Catalog",
        "stac_version": "1.0.0",
        "id": node["id"],
        "title": node["title"],
        "description": description,
        "links": links,
        "parent_ids": [parent] if parent else [],
    }


def sql_literal(value):
    return "'" + value.replace("'", "''") + "'"


def main():
    tree = yaml.safe_load((Path(__file__).parent / "catalogs.yaml").read_text())
    nodes = list(walk(tree))
    ids = [n["id"] for n, _ in nodes]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        sys.exit(f"duplicate catalog ids: {sorted(duplicates)}")
    leaves = {n["id"] for n, _ in nodes if not n.get("children")}

    collections = json.load(sys.stdin)
    # Catalogs and collections share pgstac.collections: a catalog with a
    # collection's id would replace that collection.
    clashes = set(ids) & {c["id"] for c in collections}
    if clashes:
        sys.exit(f"catalog ids that are also collection ids: {sorted(clashes)}")
    membership, unmatched = {}, []
    for c in collections:
        leaf = leaf_for(c)
        if leaf in leaves:
            membership[c["id"]] = leaf
        else:
            unmatched.append(f"  {c['id']}: {c['title']} -> {leaf}")
    if unmatched:
        sys.exit("collections without a leaf catalog:\n" + "\n".join(unmatched))

    counts = {i: 0 for i in ids}
    parents = {n["id"]: p for n, p in nodes}
    for leaf in membership.values():
        node = leaf
        while node:
            counts[node] += 1
            node = parents[node]
    empty = [i for i in ids if counts[i] == 0]
    if empty:
        sys.exit(f"catalogs with no collections: {empty}")
    depth = {}
    for n, p in nodes:
        depth[n["id"]] = depth[p] + 1 if p else 0
        print(f"{'  ' * depth[n['id']]}{n['id']} ({counts[n['id']]})", file=sys.stderr)

    id_array = "ARRAY[" + ", ".join(sql_literal(i) for i in ids) + "]::text[]"
    out = [
        "-- Generated by deploy/cscs/catalogs/load_catalogs.py from catalogs.yaml.",
        "BEGIN;",
        # Same guard as above, against the live table rather than the input list.
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pgstac.collections WHERE id = ANY ("
        + id_array
        + ") AND content->>'type' IS DISTINCT FROM 'Catalog') THEN"
        " RAISE EXCEPTION 'a catalog id is already used by a collection'; END IF; END $$;",
    ]
    for node, parent in nodes:
        doc = json.dumps(
            catalog_document(node, parent, counts[node["id"]]), ensure_ascii=False
        )
        if "$catalog$" in doc:
            sys.exit(f"{node['id']}: contains the SQL quote tag $catalog$")
        out.append(f"SELECT pgstac.upsert_collection($catalog${doc}$catalog$::jsonb);")
    out.append(
        f"DELETE FROM pgstac.collections WHERE content->>'type' = 'Catalog' AND id <> ALL ({id_array});"
    )
    values = ",\n  ".join(
        f"({sql_literal(cid)}, {sql_literal(json.dumps([leaf]))}::jsonb)"
        for cid, leaf in sorted(membership.items())
    )
    out.append(
        "UPDATE pgstac.collections c SET content = jsonb_set(c.content, '{parent_ids}', m.parent_ids)\n"
        f"FROM (VALUES\n  {values}\n) AS m(id, parent_ids)\n"
        "WHERE c.id = m.id AND c.content->>'type' = 'Collection';"
    )
    out.append("COMMIT;")
    print("\n".join(out))


if __name__ == "__main__":
    main()
