/** Remark plugin: turn the model's inline [n] citation markers into <cite>
 * nodes, so react-markdown renders each as an interactive footnote instead of
 * bare text. Mirrors the backend marker grammar (rag/citations.py: \[(\d{1,3})\]).
 *
 * findAndReplace visits `text` nodes only, so code spans (`inlineCode`/`code`)
 * are immune for free; markdown links are skipped explicitly. An invented
 * number still becomes a <cite> — CitationMark degrades it to plain text when
 * no citation carries that marker. */

import { findAndReplace } from "mdast-util-find-and-replace";
import type { PhrasingContent, Root } from "mdast";

const MARKER = /\[(\d{1,3})\]/g;

export function remarkCiteMarkers() {
  return (tree: Root): void => {
    findAndReplace(
      tree,
      [
        [
          MARKER,
          (_full: string, digits: string): PhrasingContent =>
            ({
              type: "cite",
              data: { hName: "cite", hProperties: { marker: digits } },
              children: [{ type: "text", value: `[${digits}]` }],
            }) as unknown as PhrasingContent,
        ],
      ],
      { ignore: ["link", "linkReference"] },
    );
  };
}
