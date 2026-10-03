// Parser engines a job can request. The ids are stored on parse jobs and sent to the container,
// so they must never be renamed. A contract test keeps this list identical to the Python registry
// in backend/src/parserium_collector/features/analysis/engines/.
export const DEFAULT_ENGINE='liteparse';

export const ENGINES=Object.freeze([
 Object.freeze({id:'liteparse',label:'LiteParse',ocr:true,license:'Apache-2.0',
  description:'Layout-aware extraction with table detection. Runs OCR only on pages without a text layer, so scanned pages are readable but their tables are returned as text.'}),
 Object.freeze({id:'markitdown',label:'MarkItDown',ocr:false,license:'MIT',
  description:'Lightweight, fast extraction for documents with a text layer. Detects simple tables. No OCR, so scanned pages produce no text.'})
]);

const IDS=new Set(ENGINES.map(engine=>engine.id));

export const isEngine=value=>typeof value==='string' && IDS.has(value);
