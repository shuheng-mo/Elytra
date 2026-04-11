import * as React from 'react';
import { cva } from 'class-variance-authority';
import { cn } from '../../lib/utils';

const badgeVariants = cva(
  'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium transition-colors focus:outline-none',
  {
    variants: {
      variant: {
        default:
          'border-transparent bg-[var(--accent-primary)]/15 text-[var(--accent-primary)]',
        secondary:
          'border-transparent bg-[var(--bg-tertiary)] text-[var(--text-secondary)]',
        success:
          'border-transparent bg-[var(--accent-success)]/15 text-[var(--accent-success)]',
        warning:
          'border-transparent bg-[var(--accent-warning)]/15 text-[var(--accent-warning)]',
        error:
          'border-transparent bg-[var(--accent-error)]/15 text-[var(--accent-error)]',
        outline: 'border-[var(--border-color)] text-[var(--text-secondary)]',
      },
    },
    defaultVariants: { variant: 'default' },
  }
);

function Badge({ className, variant, ...props }) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
