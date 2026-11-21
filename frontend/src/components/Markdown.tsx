import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Renders an agent's reply as Markdown, so bold, lists, headings, tables and code read as they should.
 *
 * Safety: the reply is untrusted. Raw HTML in it is not rendered (react-markdown does not parse HTML without an
 * extra plugin). Images are shown as their alt text, so a reply cannot load a remote picture. Links open in a new
 * tab, and react-markdown drops unsafe link targets such as javascript: URLs.
 */
const components: Components = {
  p: (props) => <p className="whitespace-pre-wrap" {...props} />,
  h1: (props) => <h1 className="text-base font-semibold" {...props} />,
  h2: (props) => <h2 className="text-sm font-semibold" {...props} />,
  h3: (props) => <h3 className="text-sm font-semibold" {...props} />,
  ul: (props) => <ul className="list-disc space-y-1 pl-5" {...props} />,
  ol: (props) => <ol className="list-decimal space-y-1 pl-5" {...props} />,
  blockquote: (props) => <blockquote className="border-l-4 border-stone-300 pl-3 text-stone-700" {...props} />,
  a: ({ href, children }) => (
    <a className="text-amber-800 underline" href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  img: ({ alt }) => <span className="italic text-stone-500">[image{alt ? `: ${alt}` : ""}]</span>,
  pre: (props) => <pre className="overflow-x-auto rounded bg-stone-100 p-3 font-mono text-xs" {...props} />,
  code: (props: ComponentPropsWithoutRef<"code">) => <code className="font-mono text-xs" {...props} />,
  table: (props) => (
    <div className="overflow-x-auto">
      <table className="border-collapse text-xs" {...props} />
    </div>
  ),
  th: (props) => <th className="border border-stone-300 bg-stone-50 px-2 py-1 text-left font-semibold" {...props} />,
  td: (props) => <td className="border border-stone-300 px-2 py-1" {...props} />,
};

export function Markdown({ text }: { text: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
