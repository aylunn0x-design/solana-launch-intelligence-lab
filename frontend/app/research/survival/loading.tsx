import { Skeleton, SkeletonPanel } from "@/components/ui/primitives";

export default function Loading() {
  return (
    <div className="space-y-5">
      <div className="space-y-2 border-b border-border pb-5">
        <Skeleton className="h-6 w-56" />
        <Skeleton className="h-3.5 w-full max-w-2xl" />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <SkeletonPanel rows={5} />
        <SkeletonPanel rows={5} />
      </div>
      <SkeletonPanel rows={6} />
    </div>
  );
}
