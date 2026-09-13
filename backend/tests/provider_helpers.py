from dataclasses import dataclass, field

from app.agents.llm import AttemptStats, ProviderReply
from app.agents.provider_schemas import CompileDraft, JsonAtom, JsonEntry


def encode_value(value):
    fields = dict(
        string_value=None,
        number_value=None,
        boolean_value=None,
        array_value=None,
        object_value=None,
    )
    if value is None:
        kind = "null"
    elif isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, str):
        kind = "string"
    elif isinstance(value, (float, int)):
        kind = "number"
    elif isinstance(value, list):
        kind, value = "array", [encode_value(item) for item in value]
    else:
        kind, value = "object", encode_map(value)
    if kind != "null":
        fields[f"{kind}_value"] = value
    return JsonAtom(kind=kind, **fields)


def encode_map(value):
    return [JsonEntry(key=key, value=encode_value(item)) for key, item in value.items()]


def compile_draft(graph):
    nodes = []
    for node in graph.nodes:
        requirements = []
        for req in graph.evidence_requirements:
            if req.node_id == node.id:
                requirements.append(
                    dict(
                        ref=req.id,
                        type=req.type,
                        description=req.description,
                        source_apps=req.source_apps,
                        required_fields=encode_map(req.required_fields),
                        must_all_match=req.must_all_match,
                    )
                )
        nodes.append(
            dict(
                ref=node.id,
                title=node.title,
                description=node.description,
                owner=node.owner,
                deadline=node.deadline.isoformat() if node.deadline else None,
                depends_on=node.depends_on,
                evidence_requirements=requirements,
                recovery_strategy=node.recovery_strategy,
                metadata=encode_map(node.metadata),
            )
        )
    return CompileDraft(
        title=graph.loop.title,
        goal=graph.loop.goal,
        root_ref=graph.loop.root_node_id,
        nodes=nodes,
        proposed_actions=[
            dict(
                ref=action.id,
                node_ref=action.node_id,
                app=action.app,
                action_type=action.action_type,
                parameters=encode_map(action.parameters),
                risk_level=action.risk_level,
                requires_approval=action.requires_approval,
                verification_method=action.verification_method,
            )
            for action in graph.proposed_actions
        ],
        assumptions=graph.assumptions,
        clarification_needed=graph.clarification_needed,
        clarification_question=graph.clarification_question,
    )


@dataclass
class FakeProvider:
    outputs: list
    calls: list = field(default_factory=list)

    async def generate(self, call, output_type):
        self.calls.append(call)
        result = self.outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return ProviderReply(
            result, AttemptStats("fake-model", 1.0, input_tokens=10, output_tokens=5)
        )
