import { useEffect, useRef, useState, type DragEvent, type InputHTMLAttributes } from "react";

import { uploadPath, type UploadResult } from "../api";
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
    Object.defineProperty(file, "webkitRelativePath", { configurable: true, value: entry.fullPath.replace(/^\/+/, "") });
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
    .map((item) => (item as unknown as { webkitGetAsEntry?: () => DropEntry | null }).webkitGetAsEntry?.() ?? null)
    .filter((entry): entry is DropEntry => Boolean(entry));
  return entries.length ? (await Promise.all(entries.map(filesFromEntry))).flat() : Array.from(dataTransfer.files ?? []);
}

export function ImportPanel({
  onUpload,
  onSkuImport,
  onImported,
  disabled,
}: {
  onUpload: (files: File[], mode: ImportMode) => Promise<UploadResult>;
  onSkuImport: (skus: string[], mode: ImportMode) => Promise<unknown>;
  onImported: () => void;
  disabled?: boolean;
}) {
  const imagePicker = useRef<HTMLInputElement>(null);
  const folderPicker = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<"images" | "erp">("images");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [skuText, setSkuText] = useState("");
  const skuLines = () => skuText.split(/\s+/).map((item) => item.trim()).filter(Boolean);
  const addFiles = (nextFiles: File[]) => setFiles((current) => {
    const merged = new Map(current.map((file) => [`${uploadPath(file)}:${file.size}:${file.lastModified}`, file]));
    nextFiles.forEach((file) => merged.set(`${uploadPath(file)}:${file.size}:${file.lastModified}`, file));
    return Array.from(merged.values());
  });
  const dropFiles = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    addFiles(await collectDroppedFiles(event.dataTransfer));
  };
  const uploadFiles = async (mode: ImportMode) => {
    try {
      const result = await onUpload(files, mode);
      const rejected = new Set(result.rejected.map((item) => item.filename));
      setFiles((current) => current.filter((file) => rejected.has(uploadPath(file))));
      if (!result.rejected.length) onImported();
    } catch {
      // The workbench keeps pending files visible for a retry.
    }
  };
  const importSkuList = async (mode: ImportMode) => {
    const skus = skuLines().slice(0, 50);
    if (!skus.length) return;
    await onSkuImport(skus, mode);
    setSkuText("");
    onImported();
  };
  const imageFiles = files.filter((file) => !uploadPath(file).toLowerCase().endsWith(".txt"));
  const txtCount = files.length - imageFiles.length;

  return <section className="surface overflow-visible p-5" aria-label="添加商品面板">
    <div className="mb-4 flex gap-2 border-b border-slate-100">
      <button type="button" className={`pb-3 text-sm font-semibold ${tab === "images" ? "border-b-2 border-indigo-600 text-indigo-700" : "text-slate-500"}`} onClick={() => setTab("images")}>图片 / 文件夹</button>
      <button type="button" className={`pb-3 text-sm font-semibold ${tab === "erp" ? "border-b-2 border-indigo-600 text-indigo-700" : "text-slate-500"}`} onClick={() => setTab("erp")}>ERP SKU</button>
    </div>
    {tab === "images" ? <>
      <div className="relative">
        <button className="secondary-button" type="button" aria-expanded={pickerOpen} onClick={() => setPickerOpen((open) => !open)}>添加图片或文件夹</button>
        {pickerOpen && <div className="absolute z-10 mt-2 w-48 rounded-xl border border-slate-200 bg-white p-2 shadow-lg">
          <button className="w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-50" type="button" onClick={() => imagePicker.current?.click()}>选择图片</button>
          <button className="w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-50" type="button" onClick={() => folderPicker.current?.click()}>选择文件夹</button>
        </div>}
      </div>
      <div className="mt-4 rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 p-6 text-center" onDragOver={(event) => event.preventDefault()} onDrop={dropFiles}>
        <p className="font-medium text-slate-700">拖入图片或文件夹</p>
        <p className="mt-1 text-xs text-slate-500">JPEG、PNG、WebP、UTF-8 TXT</p>
      </div>
      <input ref={imagePicker} className="sr-only" aria-label="选择图片" type="file" multiple accept={acceptedTypes} onChange={(event) => { addFiles(Array.from(event.target.files ?? [])); event.currentTarget.value = ""; }} />
      <input ref={folderPicker} className="sr-only" aria-label="选择文件夹" type="file" multiple accept={acceptedTypes} {...folderInputProps} onChange={(event) => { addFiles(Array.from(event.target.files ?? [])); event.currentTarget.value = ""; }} />
      {imageFiles.length > 0 && <div className="mt-4 grid grid-cols-5 gap-2 sm:grid-cols-8">{imageFiles.map((file, index) => <PendingImage key={`${uploadPath(file)}:${file.size}:${file.lastModified}`} file={file} index={index} />)}</div>}
      <p className="mt-4 text-sm text-slate-500">{files.length ? `已选择 ${imageFiles.length} 张图片${txtCount ? `、${txtCount} 个 TXT` : ""}` : "尚未选择图片"}</p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button className="primary-button" disabled={disabled || !files.length} onClick={() => void uploadFiles("auto")}>导入并自动出图</button>
        <button className="secondary-button" disabled={disabled || !files.length} onClick={() => void uploadFiles("organize")}>导入后整理</button>
        {files.length > 0 && <button className="text-sm font-semibold text-slate-600" type="button" onClick={() => setFiles([])}>清空</button>}
      </div>
    </> : <>
      <label className="block text-sm font-medium text-slate-700"><span className="mb-2 block">ERP SKU</span><textarea value={skuText} onChange={(event) => setSkuText(event.target.value)} placeholder="每行或空格分隔，单次最多 50 个" /></label>
      <div className="mt-4 flex flex-wrap gap-2">
        <button className="primary-button" disabled={disabled || !skuLines().length} onClick={() => void importSkuList("auto")}>导入并自动出图</button>
        <button className="secondary-button" disabled={disabled || !skuLines().length} onClick={() => void importSkuList("organize")}>导入后整理</button>
      </div>
    </>}
  </section>;
}

function PendingImage({ file, index }: { file: File; index: number }) {
  const [source] = useState(() => typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : "data:image/gif;base64,R0lGODlhAQABAAAAACw=");
  useEffect(() => () => { if (source.startsWith("blob:") && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(source); }, [source]);
  return <img className="aspect-square w-full rounded-lg border border-slate-200 object-cover" src={source} alt={`待导入商品图 ${index + 1}`} />;
}
