// Share local OCR between automatic word hit areas and current-page translation.
const pages = new Map();
export function recognizePage(book, page) {
  const key = `${book.edition_id}:${page}`;
  if (!pages.has(key)) {
    const task = fetch(`/api/page-text/${encodeURIComponent(book.book_id)}/${page}`)
      .then(async response => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || '本页文字识别暂不可用');
        // Keep concurrent readers on one request, but let a later retry recover
        // after an empty OCR result instead of retaining it until page reload.
        if (!result.items?.length) pages.delete(key);
        return result;
      }).catch(error => { pages.delete(key); throw error; });
    pages.set(key, task);
    if (pages.size > 32) pages.delete(pages.keys().next().value);
  }
  return pages.get(key);
}

// Translation and assessment share the server's page-bound reading reference.
// Raw OCR letters and a PDF text layer cannot bypass the quality check.
export async function readingText(book, page) {
  const result = await recognizePage(book, page);
  if (result.reading?.status !== 'ready' || !result.reading.text?.trim()) {
    pages.delete(`${book.edition_id}:${page}`);
    throw new Error(result.reading?.error || '本页正文暂未识别准确，请稍后重试。');
  }
  return result;
}
