// extension/src/indexingService.ts
import { BackendClient } from './backendClient';

export interface IndexState {
  isIndexed: boolean;
  needsUpdate: boolean;
  lastIndexed?: Date;
  indexSize?: number;
}

export class IndexingService {
  private backendClient: BackendClient;

  constructor(backendClient: BackendClient) {
    this.backendClient = backendClient;
  }

  async isWorkspaceIndexed(): Promise<boolean> {
    try {
      const response = await this.backendClient.checkIndexState();
      return response.isIndexed;
    } catch (error) {
      console.error('Error checking index state:', error);
      return false;
    }
  }

  async triggerIndexing(): Promise<void> {
    try {
      await this.backendClient.triggerIndexUpdate();
    } catch (error) {
      console.error('Error triggering indexing:', error);
      throw error;
    }
  }

  async needsIndexUpdate(): Promise<boolean> {
    try {
      const response = await this.backendClient.checkIndexState();
      return response.needsUpdate;
    } catch (error) {
      console.error('Error checking if index needs update:', error);
      return false;
    }
  }

  async handleIndexUpdate(): Promise<void> {
    try {
      const needsUpdate = await this.needsIndexUpdate();
      if (needsUpdate) {
        await this.triggerIndexing();
      }
    } catch (error) {
      console.error('Error handling index update:', error);
      throw error;
    }
  }
}
