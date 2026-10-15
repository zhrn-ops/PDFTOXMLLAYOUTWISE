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
    "- What is the abstract number? Conference abstracts often print an identifier such as\n"
    "  \"4349\", \"Abstract No: 4349\", or \"(S100)\" before, inside, or next to the title line.\n"
    "  Return the bare identifier only, without labels such as \"Abstract No\".\n"
    "- What text does not belong to these metadata fields?\n"
    "\n"
    "When a block's text is a paragraph continuation (only part of a sentence or\n"
    "clause), use its paragraph_text field for full context.\n"
    "Return JSON only in this form:\n"
    "{\n"
    '  "title": {"block_ids": ["block_001"], "text": "..."},\n'
    '  "authors": {"block_ids": ["block_002", "block_003"], "text": "...", "items": ["R.M. Ram mohan", "L. Pannikodu"]},\n'
    '  "affiliations": {"block_ids": ["block_003", "block_004"], "text": "...", "items": ["Nassau University Medical Center", "East Meadow, United States of America"]},\n'
    '  "abstract": {"block_ids": ["block_004"], "text": "..."},\n'
    '  "abstract_number": {"block_ids": ["block_001"], "text": "4349"},\n'
    '  "unclassified": {"block_ids": ["block_005"], "text": "..."}\n'
    "}\n"
    "Use only block_ids that appear in the payload.\n"
    "When the abstract number appears inside the title, strip it from title.text and return it as\n"
    "abstract_number.text; it becomes the ABSN itemid, so it must never remain in the title.\n"
    "If no abstract number is present, omit abstract_number.\n"
    "structural_evidence is an advisory Docling interpretation; treat the block text and PyMuPDF layout fields as the source evidence.\n"
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
    "- What is the abstract number? Conference abstracts often print an identifier such as\n"
    "  \"4349\", \"Abstract No: 4349\", or \"(S100)\" before, inside, or next to the title line.\n"
    "  Return the bare identifier only, without labels such as \"Abstract No\".\n"
    "- What text does not belong to these metadata fields?\n"
    "\n"
    "The transcript preserves block ids inline as markdown bullets.\n"
    "Return JSON only in this form:\n"
    "{\n"
    '  "title": {"block_ids": ["block_001"], "text": "..."},\n'
    '  "authors": {"block_ids": ["block_002", "block_003"], "text": "...", "items": ["R.M. Ram mohan", "L. Pannikodu"]},\n'
    '  "affiliations": {"block_ids": ["block_003", "block_004"], "text": "...", "items": ["Nassau University Medical Center", "East Meadow, United States of America"]},\n'
    '  "abstract": {"block_ids": ["block_004"], "text": "..."},\n'
    '  "abstract_number": {"block_ids": ["block_001"], "text": "4349"},\n'
    '  "unclassified": {"block_ids": ["block_005"], "text": "..."}\n'
    "}\n"
    "Use only block_ids that appear in the transcript.\n"
    "When the abstract number appears inside the title, strip it from title.text and return it as\n"
    "abstract_number.text; it becomes the ABSN itemid, so it must never remain in the title.\n"
    "If no abstract number is present, omit abstract_number.\n"
    "If you can separate individual authors or affiliations, put them in items in reading order.\n"
    "Do not include explanations."
)

ROLE_ASSIGNMENT_PROMPT = (
    "You are a scientific document metadata extraction classifier.\n"
    "You will receive text blocks and a prior answer summary for title, authors, affiliations, abstract, abstract number, and unclassified text.\n"
    "Use the answer summary as evidence and assign exactly one role to each block_id.\n"
    "\n"
    "Roles:\n"
    "- title: the main paper title, usually the most prominent heading near the top of page 1\n"
    "- author: person names, including initials and author lists\n"
    "- affiliation: institutions, departments, cities, emails, and addresses tied to authors\n"
    "- abstract: the paper summary section before the main body\n"
    "- abstract_number: a block that holds only the conference abstract-number marker,\n"
    "  such as \"4349\", \"Abstract No: 4349\", or \"(S100)\"; it becomes the ABSN itemid\n"
    "- keywords: a keyword list, normally labelled 'Keywords:' or 'Key words:'\n"
    "- unclassified: anything that does not clearly match the roles above\n"
    "\n"
    "Rules:\n"
    "- Use only the provided block text, layout context, and answer summary.\n"
    "- structural_evidence is advisory Docling structure, not a replacement for block text or precise layout fields.\n"
    "- When a block's text is only a fragment of a longer paragraph (a continuation line),\n"
    "  use paragraph_text — the full reconstructed paragraph — for role context.\n"
    "- Prefer title for the single best title block, not subtitles or section headers.\n"
    "- Prefer author for person names or author lists. There may be multiple author blocks, and author order matters.\n"
    "- Prefer affiliation for institutional text or affiliation lists. There may be multiple affiliation blocks, and affiliation order matters.\n"
    "- Prefer abstract only for the abstract section and its heading when clearly present.\n"
    "- Prefer keywords only for an explicitly labelled keyword list.\n"
    "- A block whose only content is an abstract-number marker such as \"4349\" or \"(S100)\" is an itemid, not the title: assign abstract_number to it.\n"
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

AUTHOR_AFFILIATION_PROMPT = (
    "You are a careful parser that extracts and links authors and affiliations from\n"
    "scientific articles. Use the source text and layout as evidence; support different\n"
    "journal styles rather than assuming one fixed format.\n"
    "You receive the title and the raw author and affiliation lines of ONE article.\n"
    "Parse every author and every affiliation, normalize their names, and link\n"
    "each author to the affiliations that belong to that author. Return one JSON\n"
    "object per author and one per affiliation, even for very long lists.\n"
    "\n"
    "Rules:\n"
    "- Read all author and affiliation blocks before linking. Use reading order,\n"
    "  adjacency, line breaks, and printed markers together as evidence.\n"
    "- Preserve the authors in the order they are printed. Separate each name from\n"
    "  adjacent affiliation markers. Hyphens may join given-name initials or parts\n"
    "  of a name; do not treat a final token such as J-Y or C-S as a surname solely\n"
    "  because it is last. If name order is unclear, preserve the display name and\n"
    "  avoid inventing a given-name/surname split.\n"
    "- Return every author and affiliation as a separate array item. Do not\n"
    "  summarize, group, abbreviate, or stop early because the list is long.\n"
    "- Preserve affiliation order and include the complete department, institution,\n"
    "  city, and country text for each affiliation.\n"
    "- Author names and affiliations may share one line. First identify the author\n"
    "  names, then identify affiliation entries by their printed markers.\n"
    "- Normalize superscript and baseline digits as the same marker. Markers may be\n"
    "  (¹ means \"1\", ² means \"2\", and so on).\n"
    "- Lowercase letter markers (a, b, c, ...) may be glued to names or\n"
    "  affiliation names. Remove them from the name/institution text and use\n"
    "  them only as marker references.\n"
    "- A new marked affiliation can begin after a comma or whitespace, including\n"
    "  when its marker is glued to the first word. Split there; do not merge it with the\n"
    "  preceding affiliation. Do not split affiliation text at ordinary commas.\n"
    "- Give each affiliation its printed marker and text without the leading marker.\n"
    "- Link each author to every affiliation supported by matching printed markers.\n"
    "  Several authors may share an affiliation, and one author may have several.\n"
    "  With exactly one affiliation and no conflicting evidence, link all authors to it.\n"
    "  With several affiliations, do not assign by author order alone; leave uncertain\n"
    "  links empty instead of guessing.\n"
    "- Before returning, check that the final visible author and affiliation\n"
    "  entries from the input are represented in the arrays.\n"
    "- Do not invent authors or affiliations that are not present in the provided lines.\n"
    "- Mark the presenting/corresponding author when the source says so.\n"
    "- If the article prints no affiliations, return an empty affiliations list.\n"
    "\n"
    "Return JSON only in this form:\n"
    "{\n"
    '  "authors": [\n'
    '    {"display_name": "Jane Doe", "given_names": "Jane", "surname": "Doe", '
    '"initials": "J.", "markers": ["1"], "affiliations": ["1"], "corresponding": false},\n'
    '    {"display_name": "John Smith", "given_names": "John", "surname": "Smith", '
    '"initials": "J.", "markers": ["2"], "affiliations": ["2"], "corresponding": false}\n'
    "  ],\n"
    '  "affiliations": [\n'
    '    {"marker": "1", "text": "Department of Medicine, North University, Boston, USA"},\n'
    '    {"marker": "2", "text": "City Hospital, Chicago, USA"}\n'
    "  ]\n"
    "}\n"
    "Do not include explanations."
)

# Backward-compatible alias used by the existing heuristic/LLM refinement path.
CLASSIFICATION_PROMPT = ROLE_ASSIGNMENT_PROMPT
