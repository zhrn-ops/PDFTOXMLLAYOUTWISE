"""Prompt templates."""

QUESTION_ANSWER_PROMPT = (
    "You are a scientific document metadata extraction assistant.\n"
    "Read the provided text blocks and answer these questions from evidence in the blocks:\n"
    "- What is the title?\n"
    "- Who are the authors? There may be multiple authors, and they may appear as a list.\n"
    "  Return each author as a separate name string if possible, preserving initials and surname order.\n"
    "- What are the affiliations? There may be multiple affiliations, and they may appear as a list.\n"
    "  Return each affiliation as a separate institution string if possible.\n"
    "- What is the abstract?\n"
    "- What text does not belong to these metadata fields?\n"
    "\n"
    "Return JSON only in this form:\n"
    "{\n"
    '  "title": {"block_ids": ["block_001"], "text": "..."},\n'
    '  "authors": {"block_ids": ["block_002", "block_003"], "text": "...", "items": ["R.M. Ram mohan", "L. Pannikodu"]},\n'
    '  "affiliations": {"block_ids": ["block_003", "block_004"], "text": "...", "items": ["Nassau University Medical Center", "East Meadow, United States of America"]},\n'
    '  "abstract": {"block_ids": ["block_004"], "text": "..."},\n'
    '  "unclassified": {"block_ids": ["block_005"], "text": "..."}\n'
    "}\n"
    "Use only block_ids that appear in the payload.\n"
    "If you can separate individual authors or affiliations, put them in items in reading order.\n"
    "Do not include explanations."
)

MARKDOWN_QUESTION_ANSWER_PROMPT = (
    "You are a scientific document metadata extraction assistant.\n"
    "Read the provided markdown transcript of a PDF and answer these questions from evidence in the text:\n"
    "- What is the title?\n"
    "- Who are the authors?\n"
    "- What are the affiliations?\n"
    "- What is the abstract?\n"
    "- What text does not belong to these metadata fields?\n"
    "\n"
    "The transcript preserves block ids inline as markdown bullets.\n"
    "Return JSON only in this form:\n"
    "{\n"
    '  "title": {"block_ids": ["block_001"], "text": "..."},\n'
    '  "authors": {"block_ids": ["block_002", "block_003"], "text": "...", "items": ["R.M. Ram mohan", "L. Pannikodu"]},\n'
    '  "affiliations": {"block_ids": ["block_003", "block_004"], "text": "...", "items": ["Nassau University Medical Center", "East Meadow, United States of America"]},\n'
    '  "abstract": {"block_ids": ["block_004"], "text": "..."},\n'
    '  "unclassified": {"block_ids": ["block_005"], "text": "..."}\n'
    "}\n"
    "Use only block_ids that appear in the transcript.\n"
    "If you can separate individual authors or affiliations, put them in items in reading order.\n"
    "Do not include explanations."
)

ROLE_ASSIGNMENT_PROMPT = (
    "You are a scientific document metadata extraction classifier.\n"
    "You will receive text blocks and a prior answer summary for title, authors, affiliations, abstract, and unclassified text.\n"
    "Use the answer summary as evidence and assign exactly one role to each block_id.\n"
    "\n"
    "Roles:\n"
    "- title: the main paper title, usually the most prominent heading near the top of page 1\n"
    "- author: person names, including initials and author lists\n"
    "- affiliation: institutions, departments, cities, emails, and addresses tied to authors\n"
    "- abstract: the paper summary section before the main body\n"
    "- unclassified: anything that does not clearly match the roles above\n"
    "\n"
    "Rules:\n"
    "- Use only the provided block text, layout context, and answer summary.\n"
    "- Prefer title for the single best title block, not subtitles or section headers.\n"
    "- Prefer author for person names or author lists. There may be multiple author blocks, and author order matters.\n"
    "- Prefer affiliation for institutional text or affiliation lists. There may be multiple affiliation blocks, and affiliation order matters.\n"
    "- Prefer abstract only for the abstract section and its heading when clearly present.\n"
    "- If a block could fit more than one role, choose the most specific role.\n"
    "- If uncertain, choose unclassified.\n"
    "\n"
    "Return only valid JSON in this form:\n"
    "{\n"
    '  "assignments": [\n'
    '    {"block_id": "block_001", "role": "title", "confidence": 0.98},\n'
    '    {"block_id": "block_002", "role": "author", "confidence": 0.91}\n'
    "  ]\n"
    "}\n"
    "Do not include explanations."
)

# Backward-compatible alias used by the existing heuristic/LLM refinement path.
CLASSIFICATION_PROMPT = ROLE_ASSIGNMENT_PROMPT
