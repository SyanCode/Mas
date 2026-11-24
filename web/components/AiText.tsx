"use client";

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Text written by Claude (the robot's reports, the mascot's answers, the advisor's results), rendered as the Markdown
 * it writes: headings, bold, lists, code, tables, links. Raw HTML is not rendered (react-markdown leaves it out) and
 * links other than http(s) / mailto are dropped, so a report quoting a hostile page cannot inject anything.
 */
export function AiText({ children, className = "" }: { children: string; className?: string }) {
  return (
    <div className={`ai-text min-w-0 break-words ${className}`}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => (/^(https?:|mailto:|\/|#)/i.test(url) ? url : "")}
        components={{
          a: ({ href, children: c }) => !href ? <span>{c}</span> : <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-indigo-600 underline underline-offset-2 dark:text-indigo-300">{c}</a>,
          h1: ({ children: c }) => <p className="mt-2 font-semibold first:mt-0">{c}</p>,
          h2: ({ children: c }) => <p className="mt-2 font-semibold first:mt-0">{c}</p>,
          h3: ({ children: c }) => <p className="mt-2 font-semibold first:mt-0">{c}</p>,
          h4: ({ children: c }) => <p className="mt-2 font-medium first:mt-0">{c}</p>,
          p: ({ children: c }) => <p className="my-1.5 first:mt-0 last:mb-0">{c}</p>,
          ul: ({ children: c }) => <ul className="my-1.5 list-disc space-y-0.5 pl-5">{c}</ul>,
          ol: ({ children: c }) => <ol className="my-1.5 list-decimal space-y-0.5 pl-5">{c}</ol>,
          li: ({ children: c }) => <li className="pl-0.5">{c}</li>,
          blockquote: ({ children: c }) => <blockquote className="my-1.5 border-l-2 border-zinc-300 pl-3 text-zinc-500 dark:border-white/15">{c}</blockquote>,
          hr: () => <hr className="my-2 border-zinc-200 dark:border-white/10" />,
          code: ({ className: lang, children: c }) => lang
            ? <code className={`${lang} mono text-[0.85em]`}>{c}</code>
            : <code className="mono rounded bg-zinc-900/[0.06] px-1 py-px text-[0.85em] dark:bg-white/[0.08]">{c}</code>,
          pre: ({ children: c }) => <pre className="mono my-1.5 overflow-x-auto rounded-lg bg-zinc-900/[0.05] p-2 text-[0.8em] dark:bg-black/30">{c}</pre>,
          table: ({ children: c }) => <div className="my-1.5 overflow-x-auto"><table className="w-full border-collapse text-[0.92em]">{c}</table></div>,
          th: ({ children: c }) => <th className="border-b border-zinc-300 px-2 py-1 text-left font-medium dark:border-white/15">{c}</th>,
          td: ({ children: c }) => <td className="border-b border-zinc-200/70 px-2 py-1 align-top dark:border-white/[0.06]">{c}</td>,
          img: () => null,
        }}>
        {children}
      </Markdown>
    </div>
  );
}
