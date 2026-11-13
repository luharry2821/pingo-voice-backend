export const KNOWLEDGE_LIMIT = 20000;
export const KNOWLEDGE_STORAGE_KEY = 'cmi.knowledge.v1';
export type Attachment = { name: string; text: string; truncated: boolean };
export function limitKnowledge(name: string, text: string): Attachment {
  const normalized = text.trim();
  if (!normalized) throw new Error('No readable text found in this document.');
  return { name, text: normalized.slice(0, KNOWLEDGE_LIMIT), truncated: normalized.length > KNOWLEDGE_LIMIT };
}
export async function extractDocument(file: File): Promise<Attachment> {
  const ext = file.name.split('.').pop()?.toLowerCase();
  let text = '';
  if (ext === 'txt' || ext === 'md') text = await file.text();
  else if (ext === 'pdf') {
    const pdfjs = await import('pdfjs-dist');
    const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs?url');
    pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
    const task = pdfjs.getDocument({ data: await file.arrayBuffer() });
    const doc = await task.promise;
    try {
      for (let i = 1; i <= doc.numPages; i++) {
        const page = await doc.getPage(i);
        const content = await page.getTextContent();
        text += content.items.map((item) => 'str' in item ? item.str : '').join(' ') + '\n';
        if (text.length > KNOWLEDGE_LIMIT) break;
      }
    } finally { await task.destroy(); }
  } else if (ext === 'docx') {
    const mammoth = await import('mammoth/mammoth.browser');
    text = (await mammoth.extractRawText({ arrayBuffer: await file.arrayBuffer() })).value;
  } else throw new Error('Choose a PDF, TXT, MD, or DOCX document.');
  return limitKnowledge(file.name, text);
}
