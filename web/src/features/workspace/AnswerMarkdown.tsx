import Markdown, { type Components, type UrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "../../lib/utils";

const safeUrlTransform: UrlTransform = (value) => {
  const url = value.trim();
  return /^(https?:|mailto:)/i.test(url) ? url : "";
};

const components: Components = {
  h1: ({ children }) => (
    <h1 className="text-xl font-semibold leading-7">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-lg font-semibold leading-7">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-base font-semibold leading-6">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="text-sm font-semibold leading-6">{children}</h4>
  ),
  p: ({ children }) => (
    <p className="whitespace-pre-wrap leading-7">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="list-disc space-y-1 pl-6">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal space-y-1 pl-6">{children}</ol>
  ),
  li: ({ children }) => <li className="pl-1 leading-7">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-border pl-4 text-muted-foreground">
      {children}
    </blockquote>
  ),
  a: ({ href, children }) => href ? (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="font-medium text-primary underline underline-offset-4"
    >
      {children}
    </a>
  ) : (
    <span>{children}</span>
  ),
  code: ({ className, children }) => (
    <code
      className={cn(
        "rounded bg-muted px-1.5 py-0.5 font-mono text-[0.9em]",
        className,
      )}
    >
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="max-w-full overflow-x-auto rounded-md border bg-muted/60 p-4 text-sm leading-6 [&>code]:bg-transparent [&>code]:p-0">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="max-w-full overflow-x-auto rounded-md border">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b bg-muted/60 px-3 py-2 text-left font-medium">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b px-3 py-2 align-top">
      {children}
    </td>
  ),
  hr: () => <hr className="border-border" />,
  img: ({ alt }) => alt ? (
    <span className="text-muted-foreground">{alt}</span>
  ) : null,
};

export function AnswerMarkdown({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0 space-y-3 break-words", className)}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={safeUrlTransform}
        components={components}
      >
        {content}
      </Markdown>
    </div>
  );
}
