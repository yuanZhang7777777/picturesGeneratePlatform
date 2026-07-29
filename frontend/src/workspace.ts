export interface AssetGroup {
  id: string;
  assetIds: string[];
}

export function moveAssetToSku<T extends AssetGroup>(groups: T[], assetId: string, targetSkuId: string): T[] {
  return groups.map((group) => {
    const withoutAsset = group.assetIds.filter((id) => id !== assetId);
    return {
      ...group,
      assetIds: group.id === targetSkuId ? [...withoutAsset, assetId] : withoutAsset,
    };
  });
}
