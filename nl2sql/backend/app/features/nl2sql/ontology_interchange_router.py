"""Ontology 連携 API(業種テンプレート / OWL RDF import・export)。

肥大化した ontology_router からは独立した小 router。runtime は共有し、
生成物はすべて既存の proposal 承認フローを通る。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pr_backend_core import ApiResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.concurrency import run_sync_io

from .ontology_interchange import (
    InterchangeConversion,
    OntologyTemplateMetadata,
    RdfImportError,
    apply_template,
    export_ontology_rdf,
    get_template,
    import_rdf,
    load_templates,
)
from .ontology_router import _raise_domain_error, _run_runtime_sync, ontology_runtime

router = APIRouter(prefix="/nl2sql", tags=["nl2sql-ontology-interchange"])


class OntologyTemplateSummary(BaseModel):
    id: str
    metadata: OntologyTemplateMetadata
    entity_count: int
    relationship_count: int
    term_count: int


class OntologyTemplateListData(BaseModel):
    templates: list[OntologyTemplateSummary]


class OntologyTemplateApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrides: dict[str, str] = Field(
        default_factory=dict, description="entities[].key → OWNER.OBJECT の上書き"
    )
    dry_run: bool = False


class OntologyResolvedEntity(BaseModel):
    key: str
    object_name: str


class OntologyInterchangeApplyData(BaseModel):
    proposal_ids: list[str]
    warnings_ja: list[str]
    resolved: list[OntologyResolvedEntity]
    unresolved: list[str]
    proposal_count: int
    term_proposal_count: int


class OntologyImportData(OntologyInterchangeApplyData):
    counts: dict[str, int]


def _apply_data(
    conversion: InterchangeConversion, proposal_ids: list[str]
) -> OntologyInterchangeApplyData:
    return OntologyInterchangeApplyData(
        proposal_ids=proposal_ids,
        warnings_ja=conversion.warnings,
        resolved=[
            OntologyResolvedEntity(key=key, object_name=object_name)
            for key, object_name in sorted(conversion.resolved.items())
        ],
        unresolved=conversion.unresolved,
        proposal_count=len(conversion.drafts),
        term_proposal_count=conversion.term_count,
    )


@router.get(
    "/ontology-templates",
    response_model=ApiResponse[OntologyTemplateListData],
)
def list_ontology_templates() -> ApiResponse[OntologyTemplateListData]:
    templates = _run_runtime_sync(load_templates)
    return ApiResponse(
        data=OntologyTemplateListData(
            templates=[
                OntologyTemplateSummary(
                    id=template.id,
                    metadata=template.metadata,
                    entity_count=len(template.entities),
                    relationship_count=len(template.relationships),
                    term_count=len(template.terms),
                )
                for template in templates
            ]
        )
    )


@router.post(
    "/profiles/{profile_id}/ontology-templates/{template_id}/apply",
    response_model=ApiResponse[OntologyInterchangeApplyData],
)
def apply_ontology_template(
    profile_id: str,
    template_id: str,
    request: OntologyTemplateApplyRequest,
) -> ApiResponse[OntologyInterchangeApplyData]:
    template = get_template(template_id)
    if template is None:
        raise HTTPException(
            status_code=404,
            detail=f"オントロジーテンプレートが見つかりません。(id={template_id})",
        )
    try:
        conversion, proposal_ids = _run_runtime_sync(
            apply_template,
            ontology_runtime,
            profile_id=profile_id,
            template=template,
            overrides=request.overrides,
            dry_run=request.dry_run,
        )
        return ApiResponse(data=_apply_data(conversion, proposal_ids))
    except Exception as exc:
        _raise_domain_error(exc)


@router.get("/ontology/revisions/{revision_id}/export")
def export_ontology_revision(
    revision_id: str,
    format: Literal["rdfxml", "turtle"] = "rdfxml",
) -> Response:
    try:
        ontology = _run_runtime_sync(ontology_runtime.ontology_revision, revision_id)
        content = _run_runtime_sync(export_ontology_rdf, ontology, format=format)
    except Exception as exc:
        _raise_domain_error(exc)
    extension = "rdf" if format == "rdfxml" else "ttl"
    return Response(
        content=content,
        media_type="application/rdf+xml" if format == "rdfxml" else "text/turtle",
        headers={
            "Content-Disposition": (
                f'attachment; filename="ontology-{revision_id}.{extension}"'
            )
        },
    )


@router.post(
    "/profiles/{profile_id}/ontology-import",
    response_model=ApiResponse[OntologyImportData],
)
async def import_ontology_rdf(
    profile_id: str,
    file: Annotated[UploadFile, File()],
    terms_fallback: Annotated[bool, Form()] = True,
    dry_run: Annotated[bool, Form()] = False,
) -> ApiResponse[OntologyImportData]:
    content = await file.read()
    try:
        conversion, counts, proposal_ids = await run_sync_io(
            import_rdf,
            ontology_runtime,
            profile_id=profile_id,
            content=content,
            filename=file.filename or "",
            terms_fallback=terms_fallback,
            dry_run=dry_run,
        )
    except RdfImportError as exc:
        raise HTTPException(status_code=400, detail=exc.message_ja) from exc
    except Exception as exc:
        _raise_domain_error(exc)
    base = _apply_data(conversion, proposal_ids)
    return ApiResponse(
        data=OntologyImportData(**base.model_dump(), counts=counts)
    )
