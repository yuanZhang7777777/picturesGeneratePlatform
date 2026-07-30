import { useRef, useState, type InputHTMLAttributes } from "react";
import type { ImportMode } from "../types";

const folderInputProps = { webkitdirectory: "" } as InputHTMLAttributes<HTMLInputElement>;

export function ImportPanel({
  onUpload,
  onSkuImport,
  disabled,
}: {
  onUpload: (files: File[], mode: ImportMode) => void;
  onSkuImport: (skus: string[], mode: ImportMode) => void;
  disabled?: boolean;
}) {
  const picker = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [skuText, setSkuText] = useState("");
  const skuLines = () => skuText.split(/\s+/).map((item) => item.trim()).filter(Boolean);
  const chooseUpload = (mode: ImportMode) => {
    if (files.length) onUpload(files, mode);
    else picker.current?.click();
  };
  const chooseSku = (mode: ImportMode) => {
    const skus = skuLines().slice(0, 50);
    if (skus.length) onSkuImport(skus, mode);
  };

  return (
    <section className="grid gap-4 lg:grid-cols-2">
      <article className="surface p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="font-semibold">上传图片 / 文件夹</h2>
            <p className="mt-1 text-sm text-slate-500">每张图片默认形成一个商品；TXT 作为项目风格。</p>
          </div>
          <button className="secondary-button" type="button" onClick={() => picker.current?.click()}>选择素材</button>
        </div>
        <input
          ref={picker}
          className="sr-only"
          aria-label="选择图片或文件夹"
          type="file"
          multiple
          {...folderInputProps}
          onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
        />
        <p className="mt-4 text-sm text-slate-500">{files.length ? `已选择 ${files.length} 个文件` : "尚未选择文件"}</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="primary-button" disabled={disabled} onClick={() => chooseUpload("auto")}>导入并自动出图</button>
          <button className="secondary-button" disabled={disabled} onClick={() => chooseUpload("organize")}>导入后整理</button>
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
