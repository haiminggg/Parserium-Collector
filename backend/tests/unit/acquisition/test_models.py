from importlib.util import find_spec

import pytest
from pydantic import ValidationError


def acquisition_models():
    try:
        specification = find_spec("parserium_collector.features.acquisition.models")
    except ModuleNotFoundError:
        specification = None
    if specification is None:
        pytest.fail("acquisition models are not implemented")

    from parserium_collector.features.acquisition import models

    return models


def candidate(index: int = 0) -> dict[str, str]:
    return {
        "url": f"https://documents.example/report-{index}.pdf",
        "title": f"Report {index}",
        "document_type": "pdf",
    }


def test_collection_batch_requires_between_one_and_thirty_candidates() -> None:
    models = acquisition_models()

    with pytest.raises(ValidationError):
        models.CollectionBatchRequest(candidates=[])

    request = models.CollectionBatchRequest(candidates=[candidate(index) for index in range(30)])

    assert len(request.candidates) == 30
    with pytest.raises(ValidationError):
        models.CollectionBatchRequest(candidates=[candidate(index) for index in range(31)])


def test_collection_candidate_rejects_extra_fields() -> None:
    models = acquisition_models()

    with pytest.raises(ValidationError):
        models.CollectionCandidate(**candidate(), unexpected="value")


@pytest.mark.parametrize(
    "relative_directory",
    ["/absolute", "\\absolute", "C:\\absolute", "reports/../private", "reports\\..\\private"],
)
def test_export_request_rejects_paths_outside_the_export_root(
    relative_directory: str,
) -> None:
    models = acquisition_models()

    with pytest.raises(ValidationError):
        models.DocumentExportRequest(relative_directory=relative_directory)


def test_export_request_normalizes_a_relative_subdirectory() -> None:
    models = acquisition_models()

    request = models.DocumentExportRequest(relative_directory="  bank\\2026/reports  ")

    assert request.relative_directory == "bank/2026/reports"


def test_job_and_export_status_values_are_stable() -> None:
    models = acquisition_models()

    assert {status.value for status in models.CollectionJobStatus} == {
        "queued",
        "downloading",
        "validating",
        "completed",
        "duplicate",
        "failed",
    }
    assert {status.value for status in models.ExportStatus} == {
        "queued",
        "exporting",
        "completed",
        "failed",
    }
