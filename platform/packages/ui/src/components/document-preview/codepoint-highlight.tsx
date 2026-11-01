/** PythonのUnicode codepoint範囲を、JSのUTF-16 indexと混同せずに表示する。 */
export function CodepointHighlight({text, ranges}: {text: string; ranges: [number, number][]}) {
  const characters = Array.from(text);
  const merged: [number, number][] = [];
  for (const [start, end] of [...ranges].sort((a,b) => a[0] - b[0])) {
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > characters.length) continue;
    const last = merged.at(-1);
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start,end]);
  }
  const parts = []; let offset = 0;
  for (const [start,end] of merged) {
    parts.push(characters.slice(offset,start).join(""), <mark key={start} data-surface-tint="accent" className="rounded-sm bg-accent-muted text-fg underline decoration-accent-emphasis decoration-2 underline-offset-2 box-decoration-clone">{characters.slice(start,end).join("")}</mark>);
    offset = end;
  }
  parts.push(characters.slice(offset).join(""));
  return <>{parts}</>;
}
