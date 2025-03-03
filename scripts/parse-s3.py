#!/usr/bin/env python3

import argparse
import contextlib
import csv
import os
from collections import defaultdict
from typing import Any, Optional

import boto3
from genomehubs import utils as gh_utils


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments for specifying an S3 bucket and prefix.

    Returns:
        argparse.Namespace: An object containing the parsed command-line arguments.
    """

    parser: argparse.ArgumentParser = argparse.ArgumentParser()
    bucket: str = "molluscdb"
    prefix: str = "2024-05"
    url: str = "https://cog.sanger.ac.uk"
    config: str = "tests/integration_tests/sources/assembly-data/files.types.yaml"
    attribute: str = "files"
    xrefs: str = "scripts/dev/xrefs"

    parser.add_argument("--bucket", default=bucket)
    parser.add_argument("--prefix", default=prefix)
    parser.add_argument(
        "-u",
        "--url",
        default=url,
        help="s3 endpoint URL",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=config,
        help="path to config file",
    )
    parser.add_argument(
        "-x",
        "--xrefs",
        default=xrefs,
        help="path to xrefs directory",
    )
    parser.add_argument("--attribute", default=attribute)
    return parser.parse_args()


def extract_id(s3: boto3.resources.base.ServiceResource, bucket: str, key: str) -> str:
    """
    Extract the ID from the JSON data loaded from the specified S3 bucket and key.

    Args:
        s3 (boto3.resources.base.ServiceResource): The S3 resource object.
        bucket (str): The name of the S3 bucket.
        key (str): The key (file path) within the S3 bucket.

    Returns:
        str: The ID extracted from the JSON data.
    """
    data: dict = gh_utils.load_json_from_s3(s3, bucket, key)
    return data["id"]


def set_taxon_id(
    s3: boto3.resources.base.ServiceResource, bucket: str, prefix: str, entries: dict
):
    """
    Extract the taxon ID from the assembly information JSON data loaded from the
    specified S3 bucket and prefix.

    Args:
        s3 (boto3.resources.base.ServiceResource): The S3 resource object.
        bucket (str): The name of the S3 bucket.
        prefix (str): The prefix within the S3 bucket.
        entries (dict): A dictionary to store the extracted taxon ID.

    Returns:
        None
    """
    key = f"{prefix}assembly_info.json"
    data = gh_utils.load_json_from_s3(s3, bucket, key)
    entries["taxon_id"] = [data["taxon_id"]]


def get_run_value(
    analysis: str, s3: boto3.resources.base.ServiceResource, bucket: str, prefix: str
) -> Optional[str]:
    """
    Extract the ID from the JSON data loaded from the specified S3 bucket and key.

    Args:
        analysis (str): The analysis type.
        s3 (boto3.resources.base.ServiceResource): The S3 resource object.
        bucket (str): The name of the S3 bucket.
        prefix (str): The prefix within the S3 bucket.

    Returns:
        str: The ID extracted from the JSON data, or None if the analysis type is not
            "blobtoolkit".
    """
    if analysis == "blobtoolkit":
        key = f"{prefix}blobdir/meta.json.gz"
        return extract_id(s3, bucket, key)
    else:
        return None


def process_run_entry(
    s3: boto3.resources.base.ServiceResource,
    bucket: str,
    latest_dir: str,
    subdir: str,
    run: str,
    key: str,
    name: str,
    attribute: str,
    entries: dict[str, list],
) -> dict[str, list]:
    """
    Process a run entry for a given analysis subdirectory.

    Args:
        s3: The S3 resource object
        bucket: The name of the S3 bucket
        latest_dir: The base directory prefix
        subdir: The analysis subdirectory
        run: The run name or 'all'
        key: The S3 object key
        name: The expected filename
        attribute: The attribute name to update in entries
        entries: The dictionary to update

    Returns:
        The updated entries dictionary
    """

    if run == "all":
        if not gh_utils.check_s3_file_exists(
            s3, bucket, f"{latest_dir}{subdir}/{name}"
        ):
            return entries
    elif not gh_utils.check_s3_file_exists(
        s3, bucket, f"{latest_dir}{subdir}/{run}/{name}"
    ):
        return entries

    entries[f"{attribute}.{subdir}.{run}"].append(key)

    if run == "all":
        if not entries[f"{attribute}.{subdir}.run"]:
            entries[f"{attribute}.{subdir}.run"].append(
                get_run_value(subdir, s3, bucket, f"{latest_dir}{subdir}/")
            )
    elif run not in entries[f"{attribute}.{subdir}.run"]:
        entries[f"{attribute}.{subdir}.run"].append(run)

    return entries


def set_assembly_id(latest_dir: str, entries: dict[str, list]) -> dict[str, list]:
    """Set the assembly ID in the entries dict.

    Args:
        latest_dir: The base directory prefix
        entries: The dictionary to update

    Returns:
        The updated entries dictionary
    """

    entries["assembly_id"] = [latest_dir.split("/")[1]]
    return entries


def set_busco_counts(
    s3: boto3.resources.base.ServiceResource,
    bucket: str,
    prefix: str,
    subdir: str,
    run: str,
    entries: dict[str, list],
) -> dict[str, list]:
    """Set the BUSCO counts in the entries dict.

    Args:
        s3: The S3 resource object
        bucket: The name of the S3 bucket
        prefix: The base directory prefix
        subdir: The analysis subdirectory
        run: The run name
        entries: The dictionary to update
    """

    file = f"{prefix}{subdir}/{run}/short_summary.json"
    if not gh_utils.check_s3_file_exists(s3, bucket, file):
        return entries

    data = gh_utils.load_json_from_s3(s3, bucket, file)
    name = data["lineage_dataset"]["name"]
    statuses = {
        "complete": "Complete",
        "single": "Single copy",
        "duplicated": "Multi copy",
        "fragmented": "Fragmented",
        "missing": "Missing",
    }
    for status, key in statuses.items():
        entries[f"{name}_{status}_percent"] = data["results"][f"{key} percentage"]

    file = f"{prefix}{subdir}/{run}/full_table.tsv"
    if not gh_utils.check_s3_file_exists(s3, bucket, file):
        return entries

    rows = gh_utils.load_tsv_from_s3(s3, bucket, file, skip=2)
    ids = {
        "Complete": set(),
        "Duplicated": set(),
        "Fragmented": set(),
        "Missing": set(),
    }
    for row in rows:
        ids[row["Status"]].add(row["# Busco id"])
    entries[f"{name}_complete"] = list(ids["Complete"].union(ids["Duplicated"]))
    entries[f"{name}_duplicated"] = list(ids["Duplicated"])
    entries[f"{name}_fragmented"] = list(ids["Fragmented"])
    entries[f"{name}_missing"] = list(ids["Missing"])
    entries[f"{name}_single"] = list(ids["Complete"])

    return entries


def get_entries(
    s3: Any,
    bucket: str,
    assembly_dir: str,
    subdirs: list[str],
    file_paths: dict[str, Any],
    attribute: str,
) -> dict[str, list[Any]]:
    """Get entries for the given assembly directory.

    Args:
        s3: S3 client object
        bucket: S3 bucket name
        assembly_dir: Base assembly directory
        subdirs: List of analysis subdirectories
        file_paths: Dict mapping subdir to file paths
        attribute: Attribute name to update

    Returns:
        Dict mapping attributes to values
    """

    entries: dict[str, list[Any]] = defaultdict(list)
    try:
        set_assembly_id(assembly_dir, entries)
        set_taxon_id(s3, bucket, assembly_dir, entries)
    except Exception:
        return entries
    for subdir in subdirs:
        if subdir not in file_paths:
            continue
        if "all" in file_paths[subdir]:
            runs = ["all"]
        else:
            runs = gh_utils.list_subdirectories(s3, bucket, f"{assembly_dir}{subdir}/")
        for key, value in file_paths[subdir].items():
            if not isinstance(value, dict):
                continue
            name = value["name"]
            if runs:
                for run in runs:
                    process_run_entry(
                        s3,
                        bucket,
                        assembly_dir,
                        subdir,
                        run,
                        key,
                        name,
                        attribute,
                        entries,
                    )
        if subdir == "busco":
            for run in runs:
                set_busco_counts(
                    s3,
                    bucket,
                    assembly_dir,
                    subdir,
                    run,
                    entries,
                )
        if f"{attribute}.{subdir}.run" in entries:
            entries[attribute].append(subdir)
    return entries


def parse_tsv(xrefs: dict, xref_file: str, xref_name: str) -> dict:
    """Parse xref TSV file.

    Args:
        xrefs: Dict of xrefs
        xref_file: Path to xref TSV file
        xref_name: Name of xref

    Returns:
        Dict mapping xref values to keys
    """

    indices = {
        "boat": {
            "taxon_id": 0,
            "assembly_id": 3,
            "xref_taxon_id": 0,
            "xref_assembly_id": 3,
        },
        "btk": {
            "taxon_id": 0,
            "assembly_id": 2,
            "xref_taxon_id": 0,
            "xref_assembly_id": 3,
        },
        "EnsemblMetazoa": {
            "taxon_id": 3,
            "assembly_id": 5,
            "xref_taxon_id": 1,
            "xref_assembly_id": 1,
        },
        "EnsemblRapid": {
            "taxon_id": 3,
            "assembly_id": 5,
            "xref_taxon_id": 1,
            "xref_assembly_id": 1,
        },
        "UCSC": {
            "taxon_id": 4,
            "assembly_id": 0,
            "xref_taxon_id": 0,
            "xref_assembly_id": 0,
        },
    }
    col_indices = indices.get(xref_name, {})
    if not col_indices:
        return {}

    with open(xref_file, "r") as f:
        delimiter = "\t" if xref_file.endswith(".tsv") else ","
        rows = csv.reader(f, delimiter=delimiter)
        for row in rows:
            with contextlib.suppress(IndexError):
                xref = {
                    "taxon_id": row[col_indices["taxon_id"]],
                    "assembly_id": row[col_indices["assembly_id"]],
                    "xref_taxon_id": row[col_indices["xref_taxon_id"]],
                    "xref_assembly_id": row[col_indices["xref_assembly_id"]],
                }
                xrefs[xref["assembly_id"]][xref_name] = xref["xref_assembly_id"]


def load_xrefs(xref_dir: str) -> dict:
    """Load xrefs from the specified directory.

    Args:
        xrefs: Path to xrefs directory

    Returns:
        Dict mapping xref names to values
    """

    xref_files = [
        f for f in os.listdir(xref_dir) if os.path.isfile(os.path.join(xref_dir, f))
    ]
    xrefs = defaultdict(dict)
    for xref_file in xref_files:
        xref_name = xref_file.split("_")[0]
        parse_tsv(xrefs, os.path.join(xref_dir, xref_file), xref_name)
    return xrefs


def main():
    """
    Main entry point for the script. Parses command line arguments, loads
    configuration, retrieves metadata, and processes the data for each assembly
    directory.

    Args:
        args (Namespace): Parsed command line arguments.

    Returns:
        None
    """

    args = parse_args()

    s3 = gh_utils.get_s3_client(args.url)

    config = gh_utils.load_yaml(args.config)
    meta = gh_utils.get_metadata(config, args.config, args.attribute)
    file_paths = meta.get("file_paths", {})

    assembly_dirs = gh_utils.get_directories_by_prefix(s3, args.bucket, args.prefix)
    xrefs = load_xrefs(args.xrefs)
    rows = []

    for assembly_dir in assembly_dirs:
        print(assembly_dir)
        subdirs = gh_utils.list_subdirectories(s3, args.bucket, assembly_dir)
        entries = get_entries(
            s3, args.bucket, assembly_dir, subdirs, file_paths, args.attribute
        )
        if entries["assembly_id"][0] in xrefs:
            entries.update(xrefs[entries["assembly_id"][0]])
            print(xrefs[entries["assembly_id"][0]])
        rows.append(entries)
    headers = gh_utils.set_headers(config)
    gh_utils.print_to_tsv(headers, rows, meta)


if __name__ == "__main__":
    main()
