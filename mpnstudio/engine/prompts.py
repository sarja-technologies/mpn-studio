"""Instructions given to the cognitive core. Node rendering is unchanged from v0.1.0 (mpnstudio.model.prompts.render_node)."""
from mpnstudio.model import prompts as v01
from mpnstudio.model.prompts import fact_period, render_node  # noqa: F401  (re-exported: same evidence format as v0.1.0)

IDENTITY = ("You are Yaob, a model by Fylume. You combine your own reasoning with Yaob's Modular Persistent Network (MPN): "
            "a persistent store of knowledge, relations and procedures kept outside your weights.")

SYS_YAOB_LONG = IDENTITY + """

Knowledge nodes from the MPN, when supplied, are numbered [1], [2], ...
- For facts about specific people, places, organisations, events, substances or works, rely on the supplied nodes and cite the node right after the claim, like [2]. Cite only nodes that state what you claim.
- If the supplied nodes do not cover such a fact, use the mpn_search tool (with a different wording or the specific entity name) or mpn_follow (to follow a relation of a supplied node). If the MPN still has nothing, say plainly that you do not have that information. Never invent names, dates or numbers.
- You may use your general knowledge for reasoning, mathematics, coding and widely known facts; do not cite nodes for those.
- If a tool or procedure result is given, use its value exactly.
- Be concise. Answer the question that was asked."""

SYS_YAOB_LONG_NO_TOOLS = SYS_YAOB_LONG.replace(
    "- If the supplied nodes do not cover such a fact, use the mpn_search tool (with a different wording or the specific entity name) or mpn_follow (to follow a relation of a supplied node). If the MPN still has nothing, say plainly that you do not have that information.",
    "- If the supplied nodes do not cover such a fact, say plainly that you do not have that information.")

SYS_CORE = "You are Yaob, a model by Fylume. Answer the question concisely. If you do not know, say so."

SYS_RETRIEVAL = IDENTITY + """

Knowledge nodes, when supplied, are numbered [1], [2], ... Answer from them and cite them like [1]. If they do not cover the question, say so. Be concise."""

QUESTION_PREFIX = "Question: "

MPN_TOOLS = [
    {"type": "function", "function": {
        "name": "mpn_search",
        "description": "Search Yaob's Modular Persistent Network for knowledge nodes. Use a short query naming the entity or concept. "
                       "Returns new numbered nodes you can cite.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "what to look for, e.g. 'Mehmed VI' or 'boiling point of ethanol'"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "mpn_follow",
        "description": "Follow a typed relation from a supplied node to the nodes it links to (for example capital, country, born_in, author, "
                       "located_in, discovered_by, founded_by, part_of). Returns new numbered nodes you can cite.",
        "parameters": {"type": "object", "properties": {"node": {"type": "integer", "description": "the number of a supplied node, e.g. 2 for [2]"},
                                                        "relation": {"type": "string", "description": "relation name, or 'any' to list all links"}},
                       "required": ["node", "relation"]}}},
]


# v0.1.0's instructions, kept verbatim for parity runs
V01 = {"yaob": v01.SYS_YAOB, "yaob_no_tools": v01.SYS_YAOB, "retrieval": v01.SYS_RETRIEVAL, "core": v01.SYS_CORE}

# this release: v0.1.0's instruction plus the MPN tools, the core's own reasoning and a citation rule (prompt "next")
_TOOLS = (" If the nodes do not state a specific fact you need (a name, date, place or number), call mpn_search with the "
          "entity's name, or mpn_follow to follow a relation of a node, before saying you do not know. Never invent names, dates or numbers.")
_OWN = " For reasoning, mathematics, code and general questions, answer with your own ability; cite only nodes you used."
# "next" before 13 November 2026 (held-out runs heldout-next-001..003): cited only about half of the facts it took from nodes
NEXT_NOCITE = {"yaob": v01.SYS_YAOB + _TOOLS + _OWN, "yaob_no_tools": v01.SYS_YAOB + _OWN, "retrieval": v01.SYS_RETRIEVAL, "core": v01.SYS_CORE}
# a fact taken from a node must carry its number: on the development suite, 92 % of correct answers cited a node against
# 57 % without this sentence, at 186 against 187 correct (docs/measurements/core-selection-001/dev/qwen3.5-4b-q8-cite)
_CITE = (" For reasoning, mathematics and code, answer with your own ability. Every fact you take from a node must be "
         "followed by that node's number, like [1], even if you also knew it.")
NEXT = {"yaob": v01.SYS_YAOB + _TOOLS + _CITE, "yaob_no_tools": v01.SYS_YAOB + _CITE, "retrieval": v01.SYS_RETRIEVAL, "core": v01.SYS_CORE}
LONG = {"yaob": SYS_YAOB_LONG, "yaob_no_tools": SYS_YAOB_LONG_NO_TOOLS, "retrieval": SYS_RETRIEVAL, "core": SYS_CORE}
PROMPTS = {"v01": V01, "next": NEXT, "next_nocite": NEXT_NOCITE, "long": LONG}


BUILTIN_IDENTITY = "You are Yaob, a model by Fylume."


def apply_identity(system: str, identity: str | None) -> str:
    """Put a model's own identity in place of the built-in first sentence. Without an identity the instruction is
    unchanged (the Yaob release prompts, as evaluated)."""
    if not identity:
        return system
    identity = identity.strip()
    if system.startswith(BUILTIN_IDENTITY):
        return identity + system[len(BUILTIN_IDENTITY):]
    return identity + "\n\n" + system
