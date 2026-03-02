interface SpinnerProps {
  size?: "sm" | "md" | "lg";
  className?: string;
}

const sizes = {
  sm: "w-4 h-4 border-[1.5px]",
  md: "w-8 h-8 border-2",
  lg: "w-10 h-10 border-[2.5px]",
};

export function Spinner({ size = "md", className = "" }: SpinnerProps) {
  return (
    <div
      className={`animate-spin rounded-full border-[#C8211B] border-t-transparent ${sizes[size]} ${className}`}
    />
  );
}

export function PageSpinner() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <Spinner size="md" />
    </div>
  );
}
