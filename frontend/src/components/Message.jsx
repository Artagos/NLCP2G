import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

// One chat bubble. User text is rendered verbatim; assistant replies are
// rendered as Markdown (bold, lists, inline code). Generated C++ is shown as a
// syntax-highlighted code block.
export default function Message({ role, content, tag, code }) {
  const isUser = role === "user";
  return (
    <div className={`msg ${isUser ? "user" : "bot"}`}>
      {tag ? <span className="tag">{tag}</span> : null}
      {isUser ? (
        <div className="content">{content}</div>
      ) : (
        <div className="content markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {content}
          </ReactMarkdown>
        </div>
      )}
      {code ? (
        <details className="code-details">
          <summary>generated C++</summary>
          <ReactMarkdown rehypePlugins={[rehypeHighlight]}>
            {"```cpp\n" + code + "\n```"}
          </ReactMarkdown>
        </details>
      ) : null}
    </div>
  );
}
