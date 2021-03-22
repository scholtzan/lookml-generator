"""Generate namespaces.yaml."""
import gzip
import json
import tarfile
import urllib.request
import warnings
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from itertools import groupby
from operator import itemgetter
from pathlib import Path
from typing import Dict, List

import click
import yaml

from .explores import explore_types

PROBE_INFO_BASE_URI = "https://probeinfo.telemetry.mozilla.org"

OMIT_VIEWS = {"deletion_request"}


def _get_first(tuple_):
    return tuple_[0]


def _get_views(uri):
    with urllib.request.urlopen(uri) as f:
        tarbytes = BytesIO(f.read())
    views = defaultdict(dict)
    with tarfile.open(fileobj=tarbytes, mode="r:gz") as tar:
        for tarinfo in tar:
            if tarinfo.name.endswith("/metadata.yaml"):
                metadata = yaml.safe_load(tar.extractfile(tarinfo.name))
                references = metadata.get("references", {})
                if "view.sql" not in references:
                    continue
                *_, project, dataset_id, view_id, _ = tarinfo.name.split("/")
                if project == "moz-fx-data-shared-prod":
                    views[dataset_id][view_id] = [
                        ref.split(".") for ref in references["view.sql"]
                    ]
    return views


def _get_explores(views: Dict[str, List[Dict[str, str]]]) -> dict:
    explores = {}
    for _, klass in explore_types.items():
        for explore in klass.from_views(views):
            explores.update(explore.to_dict())

    return explores


@click.command(help=__doc__)
@click.option(
    "--custom-namespaces",
    default=None,
    type=click.File(),
    help="Path to a custom namespaces file",
)
@click.option(
    "--generated-sql-uri",
    default="https://github.com/mozilla/bigquery-etl/archive/generated-sql.tar.gz",
    help="URI of a tar archive of the bigquery-etl generated-sql branch, which is "
    "used to list views and determine whether they reference stable tables",
)
@click.option(
    "--app-listings-uri",
    default="https://probeinfo.telemetry.mozilla.org/v2/glean/app-listings",
    help="URI for probeinfo service v2 glean app listings",
)
def namespaces(custom_namespaces, generated_sql_uri, app_listings_uri):
    """Generate namespaces.yaml."""
    warnings.filterwarnings("ignore", module="google.auth._default")

    if app_listings_uri.startswith(PROBE_INFO_BASE_URI):
        # For probe-info-service requests, add query param to bypass cloudfront cache
        app_listings_uri += f"?t={datetime.utcnow().isoformat()}"
    # define key function and reuse it for sorted and groupby
    get_app_name = itemgetter("app_name")
    with urllib.request.urlopen(app_listings_uri) as f:
        # groupby requires input be sorted by key to produce one result per key
        app_listings = sorted(json.loads(gzip.decompress(f.read())), key=get_app_name)
    view_definitions = _get_views(generated_sql_uri)
    namespaces = {}
    for app_name, group in groupby(app_listings, get_app_name):
        views = defaultdict(list)
        canonical_app_name = None
        for app in group:
            if app.get("deprecated"):
                continue
            is_release = app.get("app_channel") == "release"
            if canonical_app_name is None or is_release:
                canonical_app_name = app["canonical_app_name"]
            dataset_id = app["bq_dataset_family"]
            for view_id, references in view_definitions[dataset_id].items():
                if view_id in OMIT_VIEWS:
                    continue
                table = {"table": f"mozdata.{dataset_id}.{view_id}"}
                if "app_channel" in app:
                    table["channel"] = app["app_channel"]
                if len(references) == 1 and references[0][-2] == f"{dataset_id}_stable":
                    # view references a single table in the stable dataset
                    table["is_ping_table"] = True
                elif not is_release:
                    continue  # ignore non-ping tables from non-release datasets
                views[view_id].append(table)

        namespaces[app_name] = {
            "canonical_app_name": canonical_app_name,
            "views": dict(views),
            "explores": _get_explores(dict(views)),
        }

    if custom_namespaces is not None:
        namespaces.update(yaml.safe_load(custom_namespaces.read()))

    Path("namespaces.yaml").write_text(yaml.safe_dump(namespaces))