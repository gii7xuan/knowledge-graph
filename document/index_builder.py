"""Position-first Markdown sections and shared full-document sentences."""

import re
from .snapshot import make_snapshot, validate_snapshot, json_hash


class DocumentChunks(list):
    pass


def sentence_ranges(text, start=0, end=None):
    end = len(text) if end is None else end
    begin, cursor = start, start
    abbreviations = {"e.g", "i.e", "mr", "mrs", "dr", "prof", "fig", "eq", "vs", "etc", "no"}
    while cursor < end:
        char = text[cursor]
        boundary = char in "。！？!?；;\n"
        if char == ".":
            before, after = text[cursor - 1:cursor], text[cursor + 1:cursor + 2]
            token = re.search(r"([A-Za-z.]+)$", text[begin:cursor])
            word = token.group(1).lower() if token else ""
            decimal = before.isdigit() and after.isdigit()
            preceding = text[begin:cursor-len(word)].strip().split()
            initial = len(word) == 1 and word.isalpha() and (not preceding or preceding[-1][:1].isupper())
            abbreviation = word in abbreviations or initial or ("." in word and all(len(p) == 1 for p in word.split(".") if p))
            boundary = not decimal and not abbreviation and (not after or after.isspace() or after in '\"\')]}')
        if boundary:
            left, right = begin, cursor + 1
            while left < right and text[left].isspace(): left += 1
            while right > left and text[right - 1].isspace(): right -= 1
            if left < right:
                yield left, right
            begin = cursor + 1
        cursor += 1
    left, right = begin, end
    while left < right and text[left].isspace(): left += 1
    while right > left and text[right - 1].isspace(): right -= 1
    if left < right:
        yield left, right


def parse_structure(text, document_id):
    from .hierarchy_index import section_id_for
    lines, position, fence = [], 0, None
    for raw in text.splitlines(keepends=True):
        lines.append((position, raw))
        position += len(raw)
    headings = []
    skip = set()
    for ordinal, (offset, line) in enumerate(lines):
        fence_match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None: fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence): fence = None
            continue
        if fence or ordinal in skip:
            continue
        match = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$", line.rstrip("\n"))
        legacy = re.match(r"^###HEADER####\s*(.+?)\s*$", line.rstrip("\n"))
        if match:
            headings.append((offset, len(match.group(1)), match.group(2), offset + len(line)))
        elif legacy:
            headings.append((offset, 1, legacy.group(1), offset + len(line)))
        elif line.strip() and ordinal + 1 < len(lines) and not re.match(r"^\s*(?:[#>|]|[-*+]\s)", line):
            underline = lines[ordinal + 1][1].strip()
            if re.fullmatch(r"={3,}|-{3,}", underline):
                headings.append((offset, 1 if underline[0] == "=" else 2, line.strip(), lines[ordinal + 1][0] + len(lines[ordinal + 1][1])))
                skip.add(ordinal + 1)
    root = {"section_id": section_id_for(document_id, "正文", "preamble:0"), "title": "正文",
            "breadcrumb_path": "正文", "parent_section_id": None, "header_level": 0,
            "start": 0, "end": len(text), "body_start": 0, "section_order": 0}
    sections, stack, segments = [root], [], []
    previous_start, previous = 0, root
    for offset, level, title, body_start in headings:
        if offset > previous_start:
            segments.append((previous_start, offset, previous))
        while stack and stack[-1]["header_level"] >= level:
            stack.pop()["end"] = offset
        parent = stack[-1] if stack else None
        path = (parent["breadcrumb_path"] + " > " if parent else "") + title
        node = {"section_id": section_id_for(document_id, path, str(offset)), "title": title,
                "breadcrumb_path": path, "parent_section_id": parent["section_id"] if parent else None,
                "header_level": level, "start": offset, "end": len(text), "body_start": body_start,
                "section_order": len(sections)}
        sections.append(node)
        stack.append(node)
        previous, previous_start = node, offset
    if previous_start < len(text):
        segments.append((previous_start, len(text), previous))
    # The preamble has only its own span; actual parent sections retain subtree spans.
    root["end"] = headings[0][0] if headings else len(text)
    return sections, segments


def build_chunks(text, document_id, chunk_size, chunk_overlap, splitter_version, *, snapshot=None):
    from .hierarchy_index import chunk_id_for
    if type(chunk_size) is not int or chunk_size < 1 or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("require 0 <= chunk_overlap < chunk_size")
    snapshot = snapshot or make_snapshot(text, document_id)
    validate_snapshot(snapshot)
    if snapshot["document_id"] != document_id:
        raise ValueError("snapshot document identity mismatch")
    text = snapshot["canonical_text"]
    sections, segments = parse_structure(text, document_id)
    by_id = {s["section_id"]: s for s in sections}
    global_sentences = []
    chunks = DocumentChunks()
    for start, end, section in segments:
        sentences = []
        for left, right in sentence_ranges(text, start, end):
            sentence = {"sentence_id": "sentence_" + json_hash([snapshot["document_version"], left, right])[:20],
                        "document_start": left, "document_end": right, "text": text[left:right],
                        "section_id": section["section_id"]}
            sentences.append(sentence)
            global_sentences.append(sentence)
        ancestry, node = [], section
        while node:
            ancestry.insert(0, dict(node))
            node = by_id.get(node["parent_section_id"])
        cursor = start
        previous_stop = start
        while cursor < end:
            stop = min(end, cursor + chunk_size)
            if stop < end:
                boundaries = [s["document_end"] for s in sentences
                              if max(cursor + chunk_size // 2, previous_stop) < s["document_end"] <= stop]
                if boundaries: stop = max(boundaries)
            content = text[cursor:stop]
            local_sentences = []
            for sentence in sentences:
                left, right = max(cursor, sentence["document_start"]), min(stop, sentence["document_end"])
                if left < right:
                    local_sentences.append({**sentence, "text": text[left:right], "start": left-cursor, "end": right-cursor,
                                            "chunk_start": left-cursor, "chunk_end": right-cursor,
                                            "document_start": left, "document_end": right,
                                            "sentence_document_start": sentence["document_start"],
                                            "sentence_document_end": sentence["document_end"],
                                            "sentence_complete": left == sentence["document_start"] and right == sentence["document_end"]})
            order = len(chunks)
            chunks.append({"document_id": document_id, "document_version": snapshot["document_version"],
                           "chunk_id": chunk_id_for(document_id, section["breadcrumb_path"], content, order),
                           "section_id": section["section_id"], "parent_section_id": section["parent_section_id"],
                           "breadcrumb_path": section["breadcrumb_path"], "header_level": section["header_level"],
                           "chunk_order": order, "content": content, "sentences": local_sentences,
                           "document_start": cursor, "document_end": stop, "offset_basis": "canonical_document",
                           "section_ancestry": ancestry, "metadata": {f"Header-{n['header_level']}": n["title"] for n in ancestry if n["header_level"]},
                           "splitter_version": splitter_version})
            if stop == end: break
            previous_stop = stop
            cursor = max(cursor + 1, stop - chunk_overlap)
    for ordinal, chunk in enumerate(chunks):
        chunk["prev_chunk_id"] = chunks[ordinal-1]["chunk_id"] if ordinal else None
        chunk["next_chunk_id"] = chunks[ordinal+1]["chunk_id"] if ordinal+1 < len(chunks) else None
        # Non-overlapping ownership partitions prevent overlap from producing a second source.
        chunk["core_start"] = chunk["document_start"] if ordinal == 0 or chunks[ordinal-1]["section_id"] != chunk["section_id"] else chunks[ordinal-1]["document_end"]
        chunk["core_end"] = chunk["document_end"]
    chunks.snapshot, chunks.sections, chunks.sentences = snapshot, sections, global_sentences
    chunks.splitter_version = splitter_version
    return chunks
