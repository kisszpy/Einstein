export function PlaceholderPage({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="w-full max-w-[560px] px-6 text-center">
        <h1 className="text-[17px] font-semibold">{title}</h1>
        <p className="mt-2 text-[13px] leading-relaxed text-secondary">{desc}</p>
        <p className="mt-6 text-xs text-tertiary">按 M0 → M3 路线逐里程碑交付</p>
      </div>
    </div>
  );
}
