import { useRef, useState, type DragEvent, type InputHTMLAttributes, type KeyboardEvent } from "react";
import type { ImportMode } from "../types";

const folderInputProps = { webkitdirectory: "" } as InputHTMLAttributes<HTMLInputElement>;
const acceptedTypes = ".jpg,.jpeg,.png,.webp,.txt";

type DropEntry = {
  isFile: boolean;
  isDirectory: boolean;
  fullPath: string;
  file?: (success: (file: File) => void, error?: (error: DOMException) => void) => void;
  createReader?: () => { readEntries: (success: (entries: DropEntry[]) => void, error?: (error: DOMException) => void) => void };
};

async function filesFromEntry(entry: DropEntry): Promise<File[]> {
  if (entry.isFile && entry.file) {
    const file = await new Promise<File>((resolve, reject) => entry.file?.(resolve, reject));
    Object.defineProperty(file, "webkitRelativePath", {
      configurable: true,
      value: entry.fullPath.replace(/^\/+/, ""),
    });
    return [file];
  }
  if (!entry.isDirectory || !entry.createReader) return [];
  const reader = entry.createReader();
  const children: DropEntry[] = [];
  while (true) {
    const page = await new Promise<DropEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (!page.length) break;
    children.push(...page);
  }
  return (await Promise.all(children.map(filesFromEntry))).flat();
}

export async function collectDroppedFiles(dataTransfer: DataTransfer) {
  const entries = Array.from(dataTransfer.items ?? [])
    .map((item) => (
      item as unknown as { webkitGetAsEntry?: () => DropEntry | null }
    ).webkitGetAsEntry?.() ?? null)
    .filter((entry): entry is DropEntry => Boolean(entry));
  return entries.length ? (await Promise.all(entries.map(filesFromEntry))).flat() : Array.from(dataTransfer.files ?? []);
}

export function ImportPanel({
  onUpload,
  onSkuImport,
  disabled,
}: {
  onUpload: (files: File[], mode: ImportMode) => void;
  onSkuImport: (skus: string[], mode: ImportMode) => void;
  disabled?: boolean;
}) {
  const imagePicker = useRef<HTMLInputElement>(null);
  const folderPicker = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [skuText, setSkuText] = useState("");
  const skuLines = () => skuText.split(/\s+/).map((item) => item.trim()).filter(Boolean);
  const addFiles = (nextFiles: File[]) => {
    setFiles((current) => {
      const merged = new Map(current.map((file) => {
        const path = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
        return [`${path}:${file.size}:${file.lastModified}`, file];
      }));
      nextFiles.forEach((file) => {
        const path = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
        merged.set(`${path}:${file.size}:${file.lastModified}`, file);
      });
      return Array.from(merged.values());
    });
  };
  const dropFiles = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    addFiles(await collectDroppedFiles(event.dataTransfer));
  };
  const openImagePicker = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") imagePicker.current?.click();
  };
  const chooseSku = (mode: ImportMode) => {
    const skus = skuLines().slice(0, 50);
    if (skus.length) onSkuImport(skus, mode);
  };

  return (
    <section className="grid gap-4 lg:grid-cols-2">
      <article className="surface p-5">
        <h2 className="font-semibold">上传图片 / 文件夹</h2>
        <p className="mt-1 text-sm text-slate-500">每张图片默认形成一个商品；TXT 作为项目风格。</p>
        <div
          className="mt-4 rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 p-6 text-center"
          role="button"
          tabIndex={0}
          onDragOver={(event) => event.preventDefault()}
          onDrop={dropFiles}
          onKeyDown={openImagePicker}
        >
          <p className="font-medium text-slate-700">拖入图片或文件夹</p>
          <p className="mt-1 text-xs text-slate-500">JPEG、PNG、WebP、UTF-8 TXT</p>
          <div className="mt-4 flex justify-center gap-2">
            <button className="secondary-button" type="button" onClick={() => imagePicker.current?.click()}>选择图片</button>
            <button className="secondary-button" type="button" onClick={() => folderPicker.current?.click()}>选择文件夹</button>
          </div>
        </div>
        <input
          ref={imagePicker}
          className="sr-only"
          aria-label="选择图片"
          type="file"
          multiple
          accept={acceptedTypes}
          onChange={(event) => addFiles(Array.from(event.target.files ?? []))}
        />
        <input
          ref={folderPicker}
          className="sr-only"
          aria-label="选择文件夹"
          type="file"
          multiple
          accept={acceptedTypes}
          {...folderInputProps}
          onChange={(event) => addFiles(Array.from(event.target.files ?? []))}
        />
        <p className="mt-4 text-sm text-slate-500">{files.length ? `已选择 ${files.length} 个文件` : "尚未选择文件"}</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="primary-button" disabled={disabled || !files.length} onClick={() => onUpload(files, "auto")}>导入并自动出图</button>
          <button className="secondary-button" disabled={disabled || !files.length} onClick={() => onUpload(files, "organize")}>导入后整理</button>
          {files.length > 0 && <button className="text-sm font-semibold text-slate-600" type="button" onClick={() => setFiles([])}>清空</button>}
        </div>
      </article>
      <article className="surface p-5">
        <h2 className="font-semibold">ERP SKU</h2>
        <label className="mt-4 block text-sm font-medium text-slate-700">
          <span className="mb-2 block">ERP SKU</span>
          <textarea value={skuText} onChange={(event) => setSkuText(event.target.value)} placeholder="每行或空格分隔，单次最多 50 个" />
        </label>
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="primary-button" disabled={disabled || skuLines().length === 0} onClick={() => chooseSku("auto")}>导入并自动出图</button>
          <button className="secondary-button" disabled={disabled || skuLines().length === 0} onClick={() => chooseSku("organize")}>导入后整理</button>
        </div>
      </article>
    </section>
  );
}
