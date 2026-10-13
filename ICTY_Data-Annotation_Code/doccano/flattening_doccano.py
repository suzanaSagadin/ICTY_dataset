import json

TARGET_LABELS = {"agency", "externalization"}


def flatten_checkpoint(input_path, output_path, limit=None, require_target_label=False):
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # premature_stop.py writes a bare list of records; a raw checkpoint
    # file wraps them under "records" and also carries a top-level doc_id
    # for the whole document (records themselves never carry doc_id).
    if isinstance(data, list):
        records = data
        doc_id = None
        if records:
            print("WARNING: input is a bare records list (premature_stop.py output) "
                  "with no top-level doc_id; writing doc_id=null for every row. "
                  "Use the raw checkpoint file instead if you need doc_id.")
    else:
        records = data["records"]
        doc_id = data.get("doc_id")

    total_records = len(records)

    # Keep only utterances that contain at least one agency/externalization
    # sentence label -- an utterance whose sentences are all "none" is
    # dropped entirely, not just its non-matching sentences.
    if require_target_label:
        records = [
            record for record in records
            if any(
                sentence.get("label") in TARGET_LABELS
                for sentence in record.get("output", {}).get("sentences", [])
            )
        ]
        print(f"Filtered to {len(records)}/{total_records} utterance(s) containing "
              f"at least one {'/'.join(sorted(TARGET_LABELS))} label.")

    # Cap the number of utterances (not sentences) included, preserving
    # original order.
    if limit is not None:
        records = records[:limit]
        print(f"Limiting to first {len(records)} utterance(s).")

    flattened = []
    for record in records:
        output = record.get("output", {})
        unique_id = output.get("unique_id")
        previous_utterance = output.get("previous_utterance")
        following_utterance = output.get("following_utterance")
        sentences = output.get("sentences", [])
        for idx, sentence in enumerate(sentences):
            flattened.append({
                "text": sentence.get("text", "").strip(),
                "label": [sentence.get("label", "none")],
                "unique_id": unique_id,
                "doc_id": doc_id,
                "sentence_index": idx,
                "meta": {
                    "previous_utterance": previous_utterance,
                    "following_utterance": following_utterance
                }
            })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(flattened, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(flattened)} records to {output_path}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Flatten classification output into Doccano import format.")
    parser.add_argument("--input", required=True, help="Path to premature_stop.py output or a raw checkpoint file.")
    parser.add_argument("--output", required=True, help="Path to write the Doccano import JSON to.")
    parser.add_argument("--limit", type=int, default=None,
                         help="Only include the first N utterances (after filtering, if --require-target-label is set).")
    parser.add_argument("--require-target-label", action="store_true",
                         help="Only include utterances that have at least one 'agency' or 'externalization' "
                              "sentence label; utterances with only 'none' labels are dropped entirely.")
    args = parser.parse_args()

    flatten_checkpoint(
        args.input,
        args.output,
        limit=args.limit,
        require_target_label=args.require_target_label,
    )