import { Suspense } from "react";
import { notFound } from "next/navigation";
import { AssistantMessage } from "@/components/chat/assistant-message";
import { EvidenceContent } from "@/components/chat/evidence-content";
import type { PaperLensUIMessage } from "@/lib/types";

/**
 * DEV-ONLY fixture page for figure + table rendering.
 *
 * The backend `data-figure` / `data-table` parts may not be live yet, so this
 * route feeds hand-built FigureEvent / TableEvent samples straight into
 * <AssistantMessage> (answer flow) and <EvidenceContent> (panel body) to verify
 * rendering: aspect-ratio boxes, cited vs. candidate figures, graceful
 * broken-image fallback, flat/spanned tables, and the evidence figure image.
 *
 * Guarded with notFound() in production so it never ships in the real app.
 * Reach it at /chat/preview during `npm run dev`.
 */

// A tiny inline SVG used as a "real" figure image (renders without the backend).
const SAMPLE_IMG =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' width='480' height='270'>
       <rect width='480' height='270' fill='#e8ece8'/>
       <rect x='40' y='150' width='60' height='80' fill='#2f6d4f'/>
       <rect x='120' y='110' width='60' height='120' fill='#2f6d4f'/>
       <rect x='200' y='70' width='60' height='160' fill='#2f6d4f'/>
       <rect x='280' y='40' width='60' height='190' fill='#2f6d4f'/>
       <text x='24' y='30' font-family='monospace' font-size='18' fill='#1a1f1c'>Figure 2 — throughput</text>
     </svg>`,
  );

const message: PaperLensUIMessage = {
  id: "preview-msg",
  role: "assistant",
  parts: [
    {
      type: "text",
      text:
        "PagedAttention raises serving throughput markedly, as shown in [F2]. " +
        "The ablation across batch sizes is summarized in [T1], and the flat " +
        "comparison table [T2] lists per-model accuracy [C1].",
    },
    {
      type: "data-citation",
      data: {
        id: "cit-C1",
        marker: "C1",
        quote: "vLLM improves throughput by 2-4x over prior systems.",
        document_id: "doc-1",
        document_title: "vLLM",
        filename: "vllm-paper.pdf",
        page: 7,
        section_path: "5. Evaluation",
        content_hash: "abc123",
        verify_status: "grounded",
      },
    },
    {
      type: "data-figure",
      data: {
        id: "fig-cited",
        marker: "F2",
        label: "Figure 2",
        image_url: SAMPLE_IMG,
        page: 7,
        caption: "Serving throughput vs. batch size for vLLM and baselines.",
        width: 480,
        height: 270,
        display: "cited",
      },
    },
    {
      type: "data-figure",
      data: {
        id: "fig-candidate-ok",
        marker: null,
        label: "Figure 3",
        image_url: SAMPLE_IMG,
        page: 8,
        caption: "Memory layout of the KV cache blocks.",
        width: 480,
        height: 270,
        display: "candidate",
      },
    },
    {
      type: "data-figure",
      data: {
        id: "fig-candidate-broken",
        marker: null,
        label: "Figure 4",
        // Intentionally 404s (no backend /static yet) → graceful fallback box.
        image_url: "/static/figures/does-not-exist.png",
        page: 9,
        caption: "This image is intentionally missing.",
        width: 480,
        height: 320,
        display: "candidate",
      },
    },
    {
      type: "data-table",
      data: {
        id: "tbl-spanned",
        marker: "T1",
        label: "Table 1",
        page: 6,
        caption: "Ablation across batch sizes (spanned header).",
        structure_kind: "spanned",
        html:
          "<table><thead>" +
          "<tr><th rowspan='2'>Batch</th><th colspan='2'>vLLM</th><th colspan='2'>Baseline</th></tr>" +
          "<tr><th>Tok/s</th><th>Lat (ms)</th><th>Tok/s</th><th>Lat (ms)</th></tr>" +
          "</thead><tbody>" +
          "<tr><td>1</td><td>142</td><td>12</td><td>61</td><td>28</td></tr>" +
          "<tr><td>8</td><td>980</td><td>18</td><td>410</td><td>44</td></tr>" +
          "<tr><td>32</td><td>3120</td><td>26</td><td>1180</td><td>92</td></tr>" +
          "</tbody></table>",
        markdown: null,
      },
    },
    {
      type: "data-table",
      data: {
        id: "tbl-flat",
        marker: "T2",
        label: "Table 2",
        page: 7,
        caption: "Per-model accuracy (flat markdown).",
        structure_kind: "flat",
        html: null,
        markdown: [
          "| Model | Params | Accuracy | Latency |",
          "| --- | ---: | ---: | ---: |",
          "| BERT-base | 110M | 88.5% | 12ms |",
          "| RoBERTa | 355M | 90.2% | 21ms |",
          "| GPT-large | 1.5B | 91.8% | 44ms |",
        ].join("\n"),
      },
    },
  ],
} as PaperLensUIMessage;

export default function PreviewPage() {
  if (process.env.NODE_ENV === "production") notFound();

  // Pull the cited figure + spanned table back out for the evidence-panel demo.
  const figurePart = message.parts.find(
    (p) => p.type === "data-figure" && p.data.display === "cited",
  ) as Extract<PaperLensUIMessage["parts"][number], { type: "data-figure" }>;
  const spannedTablePart = message.parts.find(
    (p) => p.type === "data-table" && p.data.structure_kind === "spanned",
  ) as Extract<PaperLensUIMessage["parts"][number], { type: "data-table" }>;

  return (
    <Suspense fallback={null}>
      <div className="mx-auto w-full max-w-[780px] px-4 py-8">
        <div className="pl-mono-label mb-4">DEV fixture · figures &amp; tables</div>
        <AssistantMessage message={message} isStreaming={false} />

        <div className="pl-mono-label mb-3 mt-10">
          DEV fixture · evidence panel body
        </div>
        <div className="grid gap-6 md:grid-cols-2">
          <div className="rounded-lg border border-line bg-surface p-4">
            {figurePart && (
              <EvidenceContent
                evidence={{ kind: "figure", data: figurePart.data }}
              />
            )}
          </div>
          <div className="rounded-lg border border-line bg-surface p-4">
            {spannedTablePart && (
              <EvidenceContent
                evidence={{ kind: "table", data: spannedTablePart.data }}
              />
            )}
          </div>
        </div>
      </div>
    </Suspense>
  );
}
