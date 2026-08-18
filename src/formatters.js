export function createFormatters({ number }) {
  function pluralSongs(value) {
    const count = Number(value || 0);
    return `${number(count)} song${count === 1 ? '' : 's'}`;
  }


  function fileSize(value) {
    const bytes = Math.max(0, Number(value || 0));
    if (!Number.isFinite(bytes) || bytes === 0) return '0 bytes';
    const units = ['bytes', 'KB', 'MB', 'GB'];
    let amount = bytes;
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    const digits = unit === 0 || amount >= 10 ? 0 : 1;
    return `${amount.toFixed(digits)} ${units[unit]}`;
  }

  function repairChangeCount(value) {
    return Number(value?.change_count ?? value?.removed_count ?? 0);
  }

  function plannedRepairChange(value) {
    const count = repairChangeCount(value);
    const itemName = value?.item_name || 'item';
    if (value?.change_kind === 'omit_empty') {
      return `omit ${number(count)} empty optional ${itemName}${count === 1 ? '' : 's'}`;
    }
    if (value?.change_kind === 'reorder') {
      return `put ${number(count)} ${itemName}${count === 1 ? '' : 's'} into chronological order`;
    }
    if (value?.change_kind === 'normalize') {
      return `normalize ${number(count)} negative ${itemName}${count === 1 ? '' : 's'} to fret 0`;
    }
    if (value?.change_kind === 'remove_redundant') {
      return `remove ${number(count)} redundant ${itemName} ${count === 1 ? 'record' : 'records'} while preserving the matching chords`;
    }
    if (value?.change_kind === 'replace_media') {
      return 'create one standard library preview from the full song mix';
    }
    return `remove ${number(count)} redundant ${itemName} ${count === 1 ? 'copy' : 'copies'}`;
  }

  function completedRepairChange(value) {
    const count = repairChangeCount(value);
    const itemName = value?.item_name || 'item';
    if (value?.change_kind === 'omit_empty') {
      return `Omitted ${number(count)} empty optional ${itemName}${count === 1 ? '' : 's'} without deleting any musical event`;
    }
    if (value?.change_kind === 'reorder') {
      return `Reordered ${number(count)} ${itemName}${count === 1 ? '' : 's'} chronologically without deleting or altering any authored entries`;
    }
    if (value?.change_kind === 'normalize') {
      return `Normalized ${number(count)} negative ${itemName}${count === 1 ? '' : 's'} to fret 0 while preserving every other stored property`;
    }
    if (value?.change_kind === 'remove_redundant') {
      return `Removed ${number(count)} redundant ${itemName} ${count === 1 ? 'record' : 'records'} while preserving every matching chord`;
    }
    if (value?.change_kind === 'replace_media') {
      return 'Created a standard library preview from the full song mix';
    }
    if (value?.change_kind === 'combined') {
      const summaries = Array.isArray(value?.repair_summaries)
        ? value.repair_summaries.filter((item) => repairChangeCount(item) > 0)
        : [];
      if (summaries.length === 1) return completedRepairChange(summaries[0]);
      if (value?.preview_repaired) {
        const songDataChanges = Math.max(0, count - 1);
        return `Applied ${number(songDataChanges)} safe song-data ${songDataChanges === 1 ? 'change' : 'changes'} and created a standard library preview`;
      }
      return `Applied ${number(count)} safe stored ${count === 1 ? 'change' : 'changes'}`;
    }
    return `Removed ${number(count)} redundant ${itemName} ${count === 1 ? 'copy' : 'copies'}`;
  }

  function duration(value) {
    let seconds = Math.max(0, Math.round(Number(value || 0)));
    const hours = Math.floor(seconds / 3600);
    seconds -= hours * 3600;
    const minutes = Math.floor(seconds / 60);
    seconds -= minutes * 60;
    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m ${seconds}s`;
    return `${seconds}s`;
  }

  function localDateTime(value) {
    const timestamp = Number(value);
    if (!Number.isFinite(timestamp) || timestamp <= 0) return '';
    return new Date(timestamp * 1000).toLocaleString();
  }



  return {
    completedRepairChange,
    duration,
    fileSize,
    localDateTime,
    plannedRepairChange,
    pluralSongs,
    repairChangeCount,
  };
}
