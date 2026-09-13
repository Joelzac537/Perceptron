"""Goal intake -> validated outcome graph. No persistence or external execution."""

from dataclasses import dataclass
from importlib.resources import files

from app.agents.llm import Prompt, ReasoningBoundary, ReasoningResult
from app.agents.mapping import MappingContext, map_compile
from app.agents.provider_schemas import CompileDraft
from app.constants import LoopStatus, NodeStatus
from app.graph.compiler_validation import validate_compiler_result
from app.graph.schemas import CompiledGraph, CompileGoalRequest

COMPILER_VERSION = "compiler-v1"


class CompilerInputError(ValueError):
    code = "VALIDATION_ERROR"


@dataclass(frozen=True)
class OutcomeCompiler:
    """Inject the A2 boundary; its caller owns the provider's async lifecycle.

    compile() is the A -> C contract. compile_with_metadata() exposes per-call
    diagnostics without shared last-result state, so concurrent calls stay isolated.
    """

    boundary: ReasoningBoundary

    async def compile(self, request: CompileGoalRequest) -> CompiledGraph:
        return (await self.compile_with_metadata(request)).value

    async def compile_with_metadata(
        self, request: CompileGoalRequest
    ) -> ReasoningResult[CompiledGraph]:
        # Snapshot and revalidate nested mutable DTOs before the first await.
        snapshot = CompileGoalRequest.model_validate_json(request.model_dump_json())
        if snapshot.source_event is not None and snapshot.source_event.linked_loop_id is not None:
            raise CompilerInputError(
                "An event linked to a loop must use reconciliation, not intake"
            )

        def convert(draft: CompileDraft, context: MappingContext) -> CompiledGraph:
            graph = map_compile(draft, snapshot, context)
            node_ids = {
                source.ref: node.id for source, node in zip(draft.nodes, graph.nodes, strict=True)
            }
            for source, node in zip(draft.nodes, graph.nodes, strict=True):
                if "deadline_rule" in node.metadata or "prerequisite_ref" in node.metadata:
                    rule = node.metadata.get("deadline_rule")
                    ref = node.metadata.get("prerequisite_ref")
                    if (
                        not isinstance(rule, str)
                        or not rule.strip()
                        or ref not in source.depends_on
                    ):
                        raise ValueError(
                            "Relative deadline needs a rule and a direct prerequisite_ref"
                        )
                    if node.deadline is not None:
                        raise ValueError(
                            "Future relative deadline must remain unset before evidence"
                        )
                    node.metadata.pop("prerequisite_ref")
                    node.metadata["deadline_prerequisite_node_id"] = node_ids[ref]
            if graph.clarification_needed:
                # Keep the draft useful for the UI while preventing node-level scheduling.
                graph.loop.status = LoopStatus.BLOCKED
                for node in graph.nodes:
                    node.status = NodeStatus.BLOCKED
            return graph

        return await self.boundary.run(
            task="compile",
            prompt=Prompt(
                COMPILER_VERSION,
                files("app.prompts").joinpath("compiler.md").read_text(encoding="utf-8"),
            ),
            request=snapshot,
            output_type=CompileDraft,
            convert=convert,
            validate=lambda graph: validate_compiler_result(graph, snapshot),
        )
