"use client";

/**
 * The answer body: streamed prose with cited figures placed INLINE.
 *
 * A figure re-emitted as display="cited" is rendered right after the block that
 * references its `[F#]` marker, instead of always being promoted to the top of
 * the strip. Robustness:
 *   - If NO cited figure resolves to a block (the common case, or mid-stream
 *     before the marker has arrived), the prose is rendered as a SINGLE Markdown
 *     pass — byte-for-byte the previous behavior, so nothing regresses.
 *   - Any figure whose marker can't be placed (unresolved cited, or candidates)
 *     falls through to `FiguresStrip`, which keeps its existing promoted-card /
 *     "Related figures" layout. The fallback path is the OLD code path.
 *
 * Block splitting is fence-aware (see answer-layout) so fenced code blocks and
 * pipe tables are never torn apart.
 */

import { Fragment, useMemo } from "react";
import type { FigureData } from "@/lib/types";
import { Markdown } from "./markdown";
import { CitedFigure, FiguresStrip } from "./figures-strip";
import { TokenCursor } from "./status-line";
import { blockContainsMarker, splitIntoBlocks } from "./answer-layout";

interface Placement {
  blocks: string[];
  /** block index → cited figures whose marker first appears in that block. */
  perBlock: Map<number, FigureData[]>;
  /** figures NOT placed inline (candidates + unresolved cited) — go to the strip. */
  leftover: FigureData[];
}

function placeFigures(text: string, figures: FigureData[]): Placement {
  const blocks = splitIntoBlocks(text);
  const perBlock = new Map<number, FigureData[]>();
  const placedIds = new Set<string>();

  const cited = figures.filter((f) => f.display === "cited" && f.marker);
  for (const fig of cited) {
    const marker = fig.marker as string;
    for (let bi = 0; bi < blocks.length; bi++) {
      if (blockContainsMarker(blocks[bi], marker)) {
        const list = perBlock.get(bi) ?? [];
        list.push(fig);
        perBlock.set(bi, list);
        placedIds.add(fig.id); // first block only — no duplicate cards
        break;
      }
    }
  }

  const leftover = figures.filter((f) => !placedIds.has(f.id));
  return { blocks, perBlock, leftover };
}

export function AnswerBody({
  messageId,
  text,
  figures,
  isStreaming,
  suppressTables,
}: {
  messageId: string;
  text: string;
  figures: FigureData[];
  isStreaming: boolean;
  suppressTables: boolean;
}) {
  const { blocks, perBlock, leftover } = useMemo(
    () => placeFigures(text, figures),
    [text, figures],
  );

  const hasInline = perBlock.size > 0;

  return (
    <>
      {text &&
        (hasInline ? (
          // Interleaved: render each block, then any cited figure it references.
          <div className="relative max-w-[760px] text-[0.95rem] leading-7 text-ink">
            {blocks.map((block, bi) => {
              const isLast = bi === blocks.length - 1;
              return (
                <Fragment key={bi}>
                  <Markdown
                    content={block}
                    streaming={isStreaming && isLast}
                    suppressTables={suppressTables}
                  />
                  {isLast && isStreaming && <TokenCursor />}
                  {perBlock.get(bi)?.map((f) => (
                    <div key={f.id} className="my-4 max-w-[560px]">
                      <CitedFigure figure={f} messageId={messageId} />
                    </div>
                  ))}
                </Fragment>
              );
            })}
          </div>
        ) : (
          // No inline placement — single pass, identical to the prior behavior.
          <div className="relative max-w-[760px] text-[0.95rem] leading-7 text-ink">
            <Markdown
              content={text}
              streaming={isStreaming}
              suppressTables={suppressTables}
            />
            {isStreaming && <TokenCursor />}
          </div>
        ))}

      <FiguresStrip messageId={messageId} figures={leftover} />
    </>
  );
}
