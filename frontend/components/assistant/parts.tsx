"use client";

/**
 * The shared parts of section 13.3: the one file every screen takes its controls from,
 * with Flow's names and properties so that Flow's own parts can take their place.
 * Colours and text sizes come only from the section 13.4 tokens (`brand-*`, `text-ui*`).
 */

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { AlertCircle, Bot, X } from "lucide-react";

function cx(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}

// --- Buttons -------------------------------------------------------------------------------

export type BtnVariant = "primary" | "secondary" | "ghost" | "danger";
export type BtnSize = "default" | "sm";

const BTN_BASE =
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-control font-medium " +
  "transition-colors disabled:cursor-not-allowed disabled:opacity-50 " +
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-accent";

const BTN_VARIANTS: Record<BtnVariant, string> = {
  primary: "bg-brand-primary text-brand-primary-fg hover:bg-brand-primary/90",
  secondary:
    "border border-brand-border bg-brand-panel text-brand-ink hover:bg-brand-accent-soft dark:border-brand-border/40",
  ghost: "text-brand-ink hover:bg-brand-accent-soft",
  danger:
    "border border-brand-zone-act/40 bg-brand-panel text-brand-zone-act-ink hover:bg-brand-zone-act/10",
};

const BTN_SIZES: Record<BtnSize, string> = {
  default: "h-8 px-3 text-ui",
  sm: "h-7 px-2.5 text-ui-sm",
};

export type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant;
  size?: BtnSize;
  /** Makes the button a link that looks like a button. */
  href?: string;
  ref?: Ref<HTMLButtonElement>;
};

/** A button whose name is a verb. 32px high, or 28px when small, with 6px corners. */
export function Btn({
  variant = "secondary",
  size = "default",
  href,
  className,
  type = "button",
  children,
  ...rest
}: BtnProps) {
  const classes = cx(BTN_BASE, BTN_VARIANTS[variant], BTN_SIZES[size], className);
  if (href) {
    return (
      <a href={href} className={classes}>
        {children}
      </a>
    );
  }
  return (
    <button type={type} className={classes} {...rest}>
      {children}
    </button>
  );
}

export type IconBtnProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "aria-label"> & {
  /** What the button does, read out in place of the icon. */
  label: string;
  icon: ReactNode;
  size?: BtnSize;
  ref?: Ref<HTMLButtonElement>;
};

export function IconBtn({ label, icon, size = "default", className, type = "button", ...rest }: IconBtnProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      className={cx(
        BTN_BASE,
        BTN_VARIANTS.ghost,
        size === "sm" ? "h-7 w-7" : "h-8 w-8",
        "text-brand-ink-muted hover:text-brand-ink",
        className,
      )}
      {...rest}
    >
      <span aria-hidden="true">{icon}</span>
    </button>
  );
}

// --- Fields --------------------------------------------------------------------------------

const FIELD_BASE =
  // The edge is ink-muted, not border, so that a field can be seen against its panel (3:1).
  "w-full min-w-0 rounded-control border border-brand-ink-muted/80 bg-brand-panel px-2.5 text-ui text-brand-ink " +
  "placeholder:text-brand-ink-muted " +
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-accent " +
  "disabled:cursor-not-allowed disabled:opacity-60 aria-[invalid=true]:border-brand-zone-act";

export type InputProps = InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement> };

export function Input({ className, ...rest }: InputProps) {
  return <input className={cx(FIELD_BASE, "h-8", className)} {...rest} />;
}

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & { ref?: Ref<HTMLSelectElement> };

export function Select({ className, children, ...rest }: SelectProps) {
  return (
    <select className={cx(FIELD_BASE, "h-8 pr-7", className)} {...rest}>
      {children}
    </select>
  );
}

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  ref?: Ref<HTMLTextAreaElement>;
};

export function Textarea({ className, rows = 3, ...rest }: TextareaProps) {
  return <textarea rows={rows} className={cx(FIELD_BASE, "py-1.5", className)} {...rest} />;
}

export type ToggleProps = {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
  /** Shows the label beside the switch; otherwise it is only read out. */
  showLabel?: boolean;
};

/** A switch. Its state is told by the thumb's place and the words On or Off, not colour alone. */
export function Toggle({ checked, onChange, label, disabled, showLabel = true }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={showLabel ? undefined : label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="group inline-flex min-h-8 items-center gap-2 rounded-control text-ui text-brand-ink disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-accent"
    >
      <span
        aria-hidden="true"
        className={cx(
          "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors",
          checked
            ? "border-brand-success bg-brand-success"
            : "border-brand-ink-muted/60 bg-brand-ink-muted/20",
        )}
      >
        <span
          className={cx(
            "inline-block h-4 w-4 rounded-full bg-brand-panel shadow transition-transform",
            checked ? "translate-x-4" : "translate-x-0.5",
          )}
        />
      </span>
      {showLabel && <span>{label}</span>}
      <span aria-hidden="true" className="text-ui-sm text-brand-ink-muted">
        {checked ? "On" : "Off"}
      </span>
    </button>
  );
}

export type FieldProps = {
  label: string;
  help?: string;
  htmlFor: string;
  /** One plain sentence about what is wrong; linked to the control by the caller. */
  error?: string | null;
  errorId?: string;
  children: ReactNode;
  className?: string;
};

export function Field({ label, help, htmlFor, error, errorId, children, className }: FieldProps) {
  return (
    <div className={cx("flex min-w-0 flex-col gap-1", className)}>
      <label htmlFor={htmlFor} className="text-ui-sm font-medium text-brand-ink">
        {label}
      </label>
      {children}
      {help && <p className="text-ui-sm text-brand-ink-muted">{help}</p>}
      {error && <ErrorLine id={errorId}>{error}</ErrorLine>}
    </div>
  );
}

/** Buttons at the end of a form. They wrap rather than scroll at 375px. */
export function FormActions({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("flex flex-wrap items-center gap-2 pt-1", className)}>{children}</div>;
}

/** A plain sentence saying what went wrong, read out as soon as it shows. */
export function ErrorLine({ children, id }: { children: ReactNode; id?: string }) {
  return (
    <p id={id} role="alert" className="flex items-start gap-1.5 text-ui-sm text-brand-ink">
      <AlertCircle aria-hidden="true" className="mt-px h-3.5 w-3.5 shrink-0 text-brand-attention" />
      <span>{children}</span>
    </p>
  );
}

// --- Badges and avatars ---------------------------------------------------------------------

export type BadgeVariant = "default" | "success" | "accent" | "info" | "outline";

const BADGE_VARIANTS: Record<BadgeVariant, string> = {
  default: "bg-brand-ink-muted/10 text-brand-ink",
  success: "bg-brand-success/15 text-brand-ink",
  accent: "bg-brand-accent-soft text-brand-ink",
  info: "bg-brand-accent-soft-strong text-brand-ink",
  outline: "border border-brand-border text-brand-ink-muted dark:border-brand-border/40",
};

const DOT_VARIANTS: Record<BadgeVariant, string> = {
  default: "bg-brand-ink-muted",
  success: "bg-brand-success",
  accent: "bg-brand-accent",
  info: "bg-brand-gold",
  outline: "bg-brand-ink-muted",
};

export type BadgeProps = {
  variant?: BadgeVariant;
  /** A small dot before the words; the words always say what it means. */
  dot?: boolean;
  children: ReactNode;
  className?: string;
};

export function Badge({ variant = "default", dot, children, className }: BadgeProps) {
  return (
    <span
      className={cx(
        "inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-ui-sm font-medium",
        BADGE_VARIANTS[variant],
        className,
      )}
    >
      {dot && (
        <span aria-hidden="true" className={cx("h-1.5 w-1.5 shrink-0 rounded-full", DOT_VARIANTS[variant])} />
      )}
      <span className="truncate">{children}</span>
    </span>
  );
}

export type AvatarProps = {
  variant?: "default" | "accent" | "bot";
  size?: number;
  /** The name it stands for: its initials are shown and the name is read out. */
  identity: string;
};

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return (words.length > 1 ? `${words[0]![0]}${words[1]![0]}` : (words[0] ?? "?").slice(0, 2)).toUpperCase();
}

export function Avatar({ variant = "default", size = 28, identity }: AvatarProps) {
  return (
    <span
      role="img"
      aria-label={identity}
      style={{ width: size, height: size }}
      className={cx(
        "inline-flex shrink-0 select-none items-center justify-center rounded-full text-ui-xs font-semibold uppercase",
        variant === "default" && "bg-brand-ink-muted/15 text-brand-ink",
        variant === "accent" && "bg-brand-accent-soft-strong text-brand-ink",
        variant === "bot" && "bg-brand-primary text-brand-primary-fg",
      )}
    >
      {variant === "bot" ? (
        <Bot aria-hidden="true" style={{ width: size * 0.55, height: size * 0.55 }} />
      ) : (
        <span aria-hidden="true">{initials(identity)}</span>
      )}
    </span>
  );
}

// --- Layout --------------------------------------------------------------------------------

export type SecHeadProps = {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  /** The heading level; sections of a page are level 2. */
  level?: 1 | 2 | 3;
  id?: string;
};

export function SecHead({ title, description, action, level = 2, id }: SecHeadProps) {
  const Heading = `h${level}` as "h1" | "h2" | "h3";
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
      <div className="min-w-0 flex-1">
        <Heading
          id={id}
          className={level === 1 ? "text-ui-title text-brand-ink" : "text-ui-md font-semibold text-brand-ink"}
        >
          {title}
        </Heading>
        {description && <p className="text-ui text-brand-ink-muted">{description}</p>}
      </div>
      {action && <div className="flex flex-wrap items-center gap-2">{action}</div>}
    </div>
  );
}

/** A labelled group of rows or fields inside a panel. */
export function Block({ label, children, className }: { label?: string; children: ReactNode; className?: string }) {
  return (
    <div className={cx("flex flex-col gap-2", className)}>
      {label && <div className="text-ui-xs font-semibold uppercase text-brand-ink-muted">{label}</div>}
      {children}
    </div>
  );
}

export type PanelProps = {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "form";
  "aria-labelledby"?: string;
  "aria-label"?: string;
};

/** 10px corners and 18px padding. */
export function Panel({ children, className, as: Tag = "div", ...aria }: PanelProps) {
  return (
    <Tag
      {...aria}
      className={cx(
        "min-w-0 rounded-panel border border-brand-border bg-brand-panel p-panel dark:border-brand-border/15",
        className,
      )}
    >
      {children}
    </Tag>
  );
}

export function List({ children, label, className }: { children: ReactNode; label?: string; className?: string }) {
  return (
    <ul aria-label={label} className={cx("flex flex-col divide-y divide-brand-border dark:divide-brand-border/15", className)}>
      {children}
    </ul>
  );
}

export type ListRowProps = {
  avatar?: ReactNode;
  title: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
  /** Makes the row's title a button that opens it. */
  onClick?: () => void;
  /** Content shown below the row, e.g. the row opened in place. */
  children?: ReactNode;
  expanded?: boolean;
  ref?: Ref<HTMLButtonElement>;
  className?: string;
};

export function ListRow({ avatar, title, sub, right, onClick, children, expanded, ref, className }: ListRowProps) {
  const text = (
    <span className="flex min-w-0 flex-col">
      <span className="break-words text-ui text-brand-ink">{title}</span>
      {sub && <span className="break-words text-ui-sm text-brand-ink-muted">{sub}</span>}
    </span>
  );
  return (
    <li className={cx("py-2.5 first:pt-0 last:pb-0", className)}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        {avatar}
        <div className="min-w-0 flex-1 basis-48">
          {onClick ? (
            <button
              ref={ref}
              type="button"
              onClick={onClick}
              aria-expanded={expanded}
              className="w-full rounded-control text-left hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-accent"
            >
              {text}
            </button>
          ) : (
            text
          )}
        </div>
        {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
      </div>
      {children}
    </li>
  );
}

export function Kpi({ label, value, delta }: { label: string; value: ReactNode; delta?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col">
      <span className="text-ui-xs font-semibold uppercase text-brand-ink-muted">{label}</span>
      <span className="text-ui-title text-brand-ink">{value}</span>
      {delta && <span className="text-ui-sm text-brand-ink-muted">{delta}</span>}
    </div>
  );
}

/** One short line for a part with nothing in it. */
export function Empty({ icon, title }: { icon?: ReactNode; title: string }) {
  return (
    <p className="flex items-center gap-2 text-ui text-brand-ink-muted">
      {icon && <span aria-hidden="true">{icon}</span>}
      {title}
    </p>
  );
}

// --- Status colours ------------------------------------------------------------------------

export type StatusColour = "green" | "amber" | "red";

const ZONE: Record<StatusColour, { fill: string; ink: string; word: string }> = {
  green: { fill: "bg-brand-zone-good", ink: "text-brand-zone-good-ink", word: "Green" },
  amber: { fill: "bg-brand-zone-watch", ink: "text-brand-zone-watch-ink", word: "Amber" },
  red: { fill: "bg-brand-zone-act", ink: "text-brand-zone-act-ink", word: "Red" },
};

export function colourWord(colour: StatusColour): string {
  return ZONE[colour].word;
}

/** A status colour, always named in words as well as shown (section 13.2). */
export function ColourName({ colour }: { colour: StatusColour }) {
  const zone = ZONE[colour];
  return (
    <span className={cx("inline-flex items-center gap-1.5 font-medium", zone.ink)}>
      <span aria-hidden="true" className={cx("h-2.5 w-2.5 shrink-0 rounded-full", zone.fill)} />
      {zone.word}
    </span>
  );
}

// --- Dialog --------------------------------------------------------------------------------

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export type DialogProps = {
  title: string;
  description?: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  children?: ReactNode;
};

/**
 * A modal dialog: focus moves into it, Tab stays inside, Escape closes it, and closing
 * returns focus to where it was.
 */
export function Dialog({ title, description, onClose, footer, children }: DialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const box = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const first = box.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? box.current)?.focus();
    return () => {
      if (before && document.contains(before)) before.focus();
    };
  }, []);

  const onKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onCloseRef.current();
      return;
    }
    if (event.key !== "Tab" || !box.current) return;
    const items = Array.from(box.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) return;
    const first = items[0]!;
    const last = items[items.length - 1]!;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-brand-ink/40 p-3 sm:items-center">
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className="flex max-h-[90vh] w-full max-w-md flex-col gap-3 overflow-y-auto rounded-panel border border-brand-border bg-brand-panel p-panel text-brand-ink shadow-xl dark:border-brand-border/15"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id={titleId} className="text-ui-title">
            {title}
          </h2>
          <IconBtn label="Close" icon={<X className="h-4 w-4" />} onClick={onClose} size="sm" />
        </div>
        {description && (
          <p id={descriptionId} className="text-ui text-brand-ink-muted">
            {description}
          </p>
        )}
        {children}
        {footer && <FormActions className="justify-end">{footer}</FormActions>}
      </div>
    </div>
  );
}

// --- Choices -------------------------------------------------------------------------------

export type ChoiceOption<V extends string> = { value: V; label: ReactNode; description?: ReactNode };

export type ChoiceGroupProps<V extends string> = {
  legend: string;
  name: string;
  value: V | null;
  onChange: (value: V) => void;
  options: ChoiceOption<V>[];
  error?: string | null;
};

/** One choice of a few, each with its words (and its meaning) beside it. Arrow keys move. */
export function ChoiceGroup<V extends string>({ legend, name, value, onChange, options, error }: ChoiceGroupProps<V>) {
  const errorId = useId();
  return (
    <fieldset className="flex min-w-0 flex-col gap-1.5" aria-describedby={error ? errorId : undefined}>
      <legend className="mb-1 text-ui-sm font-medium text-brand-ink">{legend}</legend>
      {options.map((option) => (
        <label
          key={option.value}
          className={cx(
            "flex cursor-pointer items-start gap-2 rounded-control border px-2.5 py-1.5 text-ui",
            value === option.value
              ? "border-brand-accent bg-brand-accent-soft"
              : "border-brand-border dark:border-brand-border/40",
          )}
        >
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
            className="mt-0.5 h-4 w-4 shrink-0 accent-brand-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-accent"
          />
          <span className="flex min-w-0 flex-col">
            <span className="text-brand-ink">{option.label}</span>
            {option.description && <span className="break-words text-ui-sm text-brand-ink-muted">{option.description}</span>}
          </span>
        </label>
      ))}
      {error && <ErrorLine id={errorId}>{error}</ErrorLine>}
    </fieldset>
  );
}
