import { OperationalIcon } from "../OperationalIcon";

/** Decorative document geometry, not a sample of actual customer data or output. */
export function DocumentArtwork() {
  return (
    <div className="auth-artwork" aria-hidden="true" data-auth-enter>
      <div className="auth-paper auth-original">
        <div className="auth-paper-header"><OperationalIcon name="file-pdf" /><span>Original document</span></div>
        <div className="auth-paper-lines"><i /><i /><i /></div>
        <div className="auth-table-illustration">
          {Array.from({ length: 24 }, (_, index) => <span key={index}><i /></span>)}
        </div>
        <div className="auth-paper-lines auth-paper-footnotes"><i /><i /></div>
        <span className="auth-paper-caption">Preserve the original</span>
      </div>
      <div className="auth-output-label">Original <span>→</span> Structured output</div>
      <div className="auth-paper auth-output">
        <div className="auth-paper-header"><OperationalIcon name="document" /><span>Structured output</span></div>
        <div className="auth-output-format">Markdown</div>
        <div className="auth-code-illustration">
          <strong>## Document</strong>
          <i /><i /><i />
          <strong>| Table |</strong>
          <i /><i /><i /><i /><i />
        </div>
        <span className="auth-paper-caption">Review the extracted content</span>
      </div>
    </div>
  );
}
