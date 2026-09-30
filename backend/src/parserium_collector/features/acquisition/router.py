from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse, StreamingResponse

from parserium_collector.features.acquisition.errors import (
    CollectionJobNotFoundError,
    CollectionRetryConflictError,
    DocumentDownloadUnavailableError,
    DocumentNotFoundError,
    ExportUnavailableError,
)
from parserium_collector.features.acquisition.models import (
    CollectionBatchRequest,
    CollectionJobPageResponse,
    CollectionJobRecord,
    CollectionJobResponse,
    DocumentExportRequest,
    DocumentExportResponse,
    StoredDocumentPageResponse,
    StoredDocumentRecord,
    StoredDocumentResponse,
)
from parserium_collector.features.acquisition.pagination import (
    Page,
    PageCursor,
    decode_page_cursor,
    encode_page_cursor,
)
from parserium_collector.features.acquisition.service import AcquisitionService
from parserium_collector.features.analysis.errors import (
    AnalysisCollectionConflictError,
    AnalysisCollectionUnavailableError,
    AnalysisExpiredError,
    AnalysisNotFoundError,
)
from parserium_collector.features.analysis.models import AnalysisCollectionRequest
from parserium_collector.features.analysis.service import AnalysisService
from parserium_collector.features.session.dependencies import (
    require_authenticated_session,
    require_csrf_session,
)
from parserium_collector.features.session.models import AuthenticatedSession
from parserium_collector.features.storage.access import (
    SignedArtifactDownload,
    content_disposition,
)
from parserium_collector.features.storage.errors import ArtifactStorageError
from parserium_collector.features.storage.models import ArtifactStream

router = APIRouter(tags=["acquisition"])


def acquisition_service(request: Request) -> AcquisitionService:
    service = cast(
        AcquisitionService | None,
        getattr(request.app.state, "acquisition_service", None),
    )
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document acquisition is not configured.",
        )
    return service


def collection_response(record: object) -> CollectionJobResponse:
    return CollectionJobResponse.model_validate(record)


def document_response(record: object) -> StoredDocumentResponse:
    return StoredDocumentResponse.model_validate(record)


def export_response(record: object) -> DocumentExportResponse:
    return DocumentExportResponse.model_validate(record)


def collection_page_response(
    page: Page[CollectionJobRecord],
) -> CollectionJobPageResponse:
    return CollectionJobPageResponse(
        items=[collection_response(record) for record in page.items],
        total=page.total,
        next_cursor=(None if page.next_cursor is None else encode_page_cursor(page.next_cursor)),
    )


def document_page_response(
    page: Page[StoredDocumentRecord],
) -> StoredDocumentPageResponse:
    return StoredDocumentPageResponse(
        items=[document_response(record) for record in page.items],
        total=page.total,
        next_cursor=(None if page.next_cursor is None else encode_page_cursor(page.next_cursor)),
    )


def pagination_cursor(value: str | None) -> PageCursor | None:
    if value is None:
        return None
    try:
        return decode_page_cursor(value)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The pagination cursor is invalid.",
        ) from error


@router.post(
    "/api/v1/collection/jobs",
    response_model=list[CollectionJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="create_collection_jobs",
)
async def create_collection_jobs(
    payload: CollectionBatchRequest | AnalysisCollectionRequest,
    request: Request,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[AcquisitionService, Depends(acquisition_service)],
) -> list[CollectionJobResponse]:
    if isinstance(payload, AnalysisCollectionRequest):
        analysis = cast(
            AnalysisService | None,
            getattr(request.app.state, "analysis_service", None),
        )
        if analysis is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Analysis-based collection is not configured.",
            )
        try:
            records = await analysis.collect_candidates(
                authenticated.scope,
                payload,
                datetime.now(UTC),
            )
        except AnalysisNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Candidate analysis not found.",
            ) from error
        except AnalysisExpiredError as error:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Candidate analysis expired.",
            ) from error
        except AnalysisCollectionConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Candidate analysis is not ready for collection.",
            ) from error
        except AnalysisCollectionUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Analysis-based collection is not configured.",
            ) from error
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Analyzed document collection failed.",
            ) from error
        return [collection_response(record) for record in records]
    try:
        records = await service.create_collection(
            authenticated.scope,
            payload,
            datetime.now(UTC),
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document collection request failed.",
        ) from error
    return [collection_response(record) for record in records]


@router.get(
    "/api/v1/collection/jobs",
    response_model=CollectionJobPageResponse,
    operation_id="list_collection_jobs",
)
async def list_collection_jobs(
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(require_authenticated_session),
    ],
    service: Annotated[AcquisitionService, Depends(acquisition_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> CollectionJobPageResponse:
    decoded_cursor = pagination_cursor(cursor)
    try:
        page = await service.list_collection_jobs_page(
            authenticated.scope,
            limit,
            decoded_cursor,
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document collection status is unavailable.",
        ) from error
    return collection_page_response(page)


@router.delete(
    "/api/v1/collection/jobs/completed",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="clear_completed_collection_jobs",
)
async def clear_completed_collection_jobs(
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[AcquisitionService, Depends(acquisition_service)],
) -> Response:
    try:
        await service.clear_completed_collection_jobs(authenticated.scope)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Completed collection history could not be cleared.",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/v1/collection/jobs/{job_id}/retry",
    response_model=CollectionJobResponse,
    operation_id="retry_collection_job",
)
async def retry_collection_job(
    job_id: UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[AcquisitionService, Depends(acquisition_service)],
) -> CollectionJobResponse:
    try:
        return collection_response(
            await service.retry_collection(
                authenticated.scope,
                job_id,
                datetime.now(UTC),
            )
        )
    except CollectionJobNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection job not found.",
        ) from error
    except CollectionRetryConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Collection job is not eligible for retry.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document collection retry failed.",
        ) from error


@router.get(
    "/api/v1/documents",
    response_model=StoredDocumentPageResponse,
    operation_id="list_documents",
)
async def list_documents(
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(require_authenticated_session),
    ],
    service: Annotated[AcquisitionService, Depends(acquisition_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> StoredDocumentPageResponse:
    decoded_cursor = pagination_cursor(cursor)
    try:
        page = await service.list_documents_page(
            authenticated.scope,
            limit,
            decoded_cursor,
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored documents are unavailable.",
        ) from error
    return document_page_response(page)


@router.get(
    "/api/v1/documents/{document_id}/download",
    operation_id="download_stored_document",
)
async def download_stored_document(
    document_id: UUID,
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(require_authenticated_session),
    ],
    service: Annotated[AcquisitionService, Depends(acquisition_service)],
) -> Response:
    try:
        download = await service.download_document(authenticated.scope, document_id)
    except DocumentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from error
    except DocumentDownloadUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document download is not configured.",
        ) from error
    except ArtifactStorageError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document download is temporarily unavailable.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document download is unavailable.",
        ) from error
    artifact = download.artifact
    common_headers = {
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }
    if isinstance(artifact, SignedArtifactDownload):
        return RedirectResponse(
            artifact.target,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers=common_headers,
        )
    if not isinstance(artifact, ArtifactStream):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document download is temporarily unavailable.",
        )
    return StreamingResponse(
        artifact.body,
        media_type=artifact.media_type,
        headers={
            **common_headers,
            "Content-Length": str(artifact.size_bytes),
            "Content-Disposition": content_disposition(
                artifact.filename or download.document.safe_filename
            ),
        },
    )


@router.delete(
    "/api/v1/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_stored_document",
)
async def delete_stored_document(
    document_id: UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[AcquisitionService, Depends(acquisition_service)],
) -> Response:
    try:
        await service.delete_document(
            authenticated.scope,
            document_id,
            datetime.now(UTC),
        )
    except DocumentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document deletion failed.",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/v1/documents/{document_id}/exports",
    response_model=DocumentExportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="create_document_export",
)
async def create_document_export(
    document_id: UUID,
    payload: DocumentExportRequest,
    authenticated: Annotated[AuthenticatedSession, Depends(require_csrf_session)],
    service: Annotated[AcquisitionService, Depends(acquisition_service)],
) -> DocumentExportResponse:
    try:
        record = await service.create_export(
            authenticated.scope,
            document_id,
            payload.relative_directory,
            datetime.now(UTC),
        )
        return export_response(record)
    except DocumentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from error
    except ExportUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document export is not configured.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document export request failed.",
        ) from error


@router.get(
    "/api/v1/exports",
    response_model=list[DocumentExportResponse],
    operation_id="list_document_exports",
)
async def list_document_exports(
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(require_authenticated_session),
    ],
    service: Annotated[AcquisitionService, Depends(acquisition_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[DocumentExportResponse]:
    try:
        records = await service.list_exports(authenticated.scope, limit)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document export status is unavailable.",
        ) from error
    return [export_response(record) for record in records]
