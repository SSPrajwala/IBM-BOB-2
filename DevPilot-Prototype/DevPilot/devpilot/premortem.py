"""Pre-Mortem Intelligence: "here's how this breaks" before it does.

For the top findings (reusing blast_radius's already-computed candidates),
this builds a plain-English failure timeline and prevention framing from
data DevPilot already has -- severity, message, and real reverse-dependency
count. It does not claim to simulate a cascading production incident the
way a tool trained on real incident history could; it's an honest, static
projection of "if this specific bug fires, here's the shape of the damage."
"""
from devpilot import watsonx_client

_STAGE_TEMPLATES = {
    "sql-injection": [
        "A request arrives with a crafted value instead of ordinary input.",
        "The query executes with that value spliced directly into SQL instead of bound as a parameter.",
        "The database returns rows the caller was never meant to see -- or a second statement executes outright.",
        "By the time anyone notices, the data has already been read, altered, or exfiltrated.",
    ],
    "unchecked-lookup": [
        "A caller requests a record with an id that doesn't exist (typo, stale link, deleted row).",
        "The lookup returns nothing, but the code doesn't check for that.",
        "The next line runs against a missing result and raises an unhandled exception.",
        "The caller gets a raw server error instead of a clean 404 -- and if this endpoint is popular, it shows up as a spike in noisy error-rate alerts.",
    ],
    "insecure-deserialization": [
        "Untrusted bytes reach a pickle/marshal/unsafe-YAML call.",
        "Deserializing them executes arbitrary code as a side effect, not just data loading.",
        "Whoever controls that input now controls the process running it.",
    ],
    "hardcoded-secret": [
        "The credential sits in source control in plain text.",
        "Anyone with read access to the repo -- or its history -- has it, even after rotation, unless history is rewritten.",
        "If the repo is ever made public, or a laptop with a clone is lost, the secret leaks immediately.",
    ],
    "shell-injection": [
        "A shell command is built from a string that includes external input.",
        "That input contains shell metacharacters instead of an ordinary value.",
        "The shell interprets them, running commands nobody intended.",
    ],
}
_DEFAULT_STAGES = [
    "The flagged code path executes under real production conditions instead of the happy path it was written for.",
    "The gap DevPilot found turns from a static warning into a runtime failure.",
    "Whoever depends on this file finds out the hard way -- through an error, a bad response, or corrupted data.",
]


def _stages_for(rule):
    return _STAGE_TEMPLATES.get(rule, _DEFAULT_STAGES)


def _template_scenario(entry):
    stages = _stages_for(entry["rule"])
    timeline = [f"{i + 1}. {s}" for i, s in enumerate(stages)]
    impact = (
        f"Blast radius: {entry['dependent_count']} other file(s) depend on {entry['file']}, so the failure doesn't stay contained."
        if entry["dependent_count"] else
        f"Blast radius: nothing else in this repo imports {entry['file']} directly, so the damage stays local -- still worth fixing, just lower spread risk."
    )
    return {
        "file": entry["file"],
        "function": entry.get("function"),
        "severity": entry["severity"],
        "confirmed": entry["confirmed"],
        "title": f"{entry['file']}" + (f" :: {entry['function']}" if entry.get("function") else ""),
        "timeline": timeline,
        "impact": impact,
        "prevention": entry["message"] + " Fixing it now (see Auto-Fix) is the prevention strategy -- there's no monitoring workaround for a bug that's already in the code path.",
    }


def compute(report):
    entries = report.get("blast_radius", [])[:3]
    scenarios = [_template_scenario(e) for e in entries]

    for sc in scenarios:
        template_narrative = " ".join(s.split(". ", 1)[-1] for s in sc["timeline"])
        if watsonx_client.available():
            prompt = (
                "Rewrite this technical pre-mortem timeline as a tight, vivid but strictly accurate 3-4 sentence "
                "narrative a non-engineer could follow. Do not invent any fact beyond what's given.\n\n"
                f"{sc['title']}\n" + "\n".join(sc["timeline"]) + f"\n{sc['impact']}"
            )
            generated = watsonx_client.generate(prompt, max_tokens=180)
            if generated:
                sc["narrative"], sc["source"] = generated, "watsonx.ai"
            else:
                sc["narrative"], sc["source"] = template_narrative, "template (watsonx.ai call failed, fell back)"
        else:
            sc["narrative"], sc["source"] = template_narrative, "template"

    return scenarios
