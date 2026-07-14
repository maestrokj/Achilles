/** Trigger a browser "save file" for an in-memory blob. Used for exports that
 * ride an authorized request (Bearer), so a plain `<a href>` cannot fetch them —
 * the caller fetches the blob, we hand it to the user. */
export function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
