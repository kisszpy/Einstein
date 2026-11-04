import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ content }: { content: string }) {
  return (
    <div className="text-sm leading-relaxed [&_code]:rounded [&_code]:bg-surface-3 [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[12px] [&_h1]:mb-2 [&_h1]:mt-4 [&_h1]:text-base [&_h1]:font-medium [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-[15px] [&_h2]:font-medium [&_h3]:mb-1 [&_h3]:mt-3 [&_h3]:text-sm [&_h3]:font-medium [&_li]:my-0.5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-surface-3 [&_pre]:p-3 [&_pre]:text-[12px] [&_strong]:font-medium [&_table]:my-2 [&_table]:w-full [&_td]:border [&_td]:border-line [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-line [&_th]:px-2 [&_th]:py-1 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}
