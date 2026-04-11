import { Badge } from '../ui/badge';
import { Shield } from 'lucide-react';

export function PermissionBadge({ role }) {
  if (!role) return null;
  return (
    <Badge variant="outline" className="gap-1">
      <Shield className="h-3 w-3" />
      {role}
    </Badge>
  );
}
