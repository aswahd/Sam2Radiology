
import torch
import torch.nn as nn

from sam2rad.models.sam.modeling import PromptEncoder

from .prompt_learning import PromptPredictor


class PromptSampler(nn.Module):
    """
    A prompt sampler that samples any number of prompts from learned prompts and manual prompts (box, point, or mask).
    """

    def __init__(self, prompt_learner: PromptPredictor, prompt_encoder: PromptEncoder):
        super().__init__()
        self.prompt_learner = prompt_learner
        self.prompt_encoder = prompt_encoder
        self.p = torch.tensor([1.0, 0.0, 0.4, 0.2])
        # Augment the learned prompts with manual prompts.
        # Learned prompts will be used 0.9 of the time
        # Point will be used 0.2 of the time
        # Box will be used 0.4 of the time
        # Mask will be used 0.2 of the time

    def forward(
        self,
        image_embedding: torch.Tensor,
        learned_prompts: torch.Tensor,
        batch,
    ) -> torch.Tensor:
        return self.sample(image_embedding, learned_prompts, batch)

    def valid_sample(self, arr) -> bool:
        """
        At least one prompt should be selected.
        """
        return sum(arr) > 0

    @staticmethod
    def non_zero_coordinates(mask: torch.Tensor, num_points: int) -> torch.Tensor:
        """
        Returns coordinates for selected points based on the mask.

        - mask (Tensor): A binary mask of shape (B, H, W).
        Parameters:

        Returns:
        - Tensor: A tensor of shape (B, num_points, 2) containing the (x, y) coordinates of selected points in the batch.
        """

        batch_size = mask.size(0)
        points = []

        for i in range(batch_size):
            non_zero_points = torch.nonzero(mask[i], as_tuple=False)
            non_zero_points = non_zero_points[
                torch.randint(non_zero_points.size(0), (num_points,))
            ]
            # (row, col) -> (col, row)
            non_zero_points = non_zero_points.flip(1)
            points.append(non_zero_points)

        _points = torch.stack(points)
        return _points

    def sample(
        self,
        image_embedding: torch.Tensor,
        learnable_prompts: torch.Tensor,
        batch: torch.Tensor,
    ):
        """
        Samples prompts for mask decoder.

        Parameters:
        - image_embedding (torch.Tensor): The image embedding with shape (B, 256, H, W).
        - learned_prompts (torch.Tensor): The learned prompts with shape (B, N, 256).
        - batch: a batch of data .

        Returns:
        - torch.Tensor: The sampled output prompts with shape (B, N, 256).

        Note: This function is intended for use during training only.
        """

        masks = batch.get("masks", None)
        assert masks is not None, "Masks must be provided during training."
        # Choose from learned, point, box, mask prompts
        sampled = torch.bernoulli(self.p)
        if not self.valid_sample(sampled):  # At least one prompt should be selected
            sampled[0] = 1

        # points = batch["points"] if sampled[1] else None
        boxes = batch["boxes"] if sampled[2] else None
        _masks = batch["low_res_masks"] if sampled[3] else None
        sparse_embeddings, mask_embeddings = self.prompt_encoder(
            points=None,
            boxes=boxes,
            masks=_masks,
        )

        learned_sparse_embeddings = torch.empty(
            sparse_embeddings.size(0),
            0,
            sparse_embeddings.size(2),
            device=sparse_embeddings.device,
        )

        # If no manual prompts are provided, use only learned prompts
        if not (sparse_embeddings.size(1) > 0):
            (
                learned_sparse_embeddings,
                learned_dense_embeddings,
                interim_mask_output,
                pred_boxes,
            ) = self.prompt_learner(image_embedding, learnable_prompts)
            output_sparse_embeddings = learned_sparse_embeddings

        elif sampled[
            0
        ]:  # If manual prompts are provided, and learned prompted are sampled
            # Use both learned and manual prompts

            (
                learned_sparse_embeddings,
                learned_dense_embeddings,
                interim_mask_output,
                pred_boxes,
            ) = self.prompt_learner(image_embedding, learnable_prompts)

            output_sparse_embeddings = torch.cat(
                [learned_sparse_embeddings, sparse_embeddings], dim=1
            )

        else:  # Use only manual prompts
            output_sparse_embeddings = sparse_embeddings
            interim_mask_output = learned_dense_embeddings = pred_boxes = None

        # Use learned learned dense embeddings?
        # if random.random() < 0.1:
        #     learned_dense_embeddings = None
        #     interim_mask_output = None

        return {
            # "box_embeddings": sparse_embeddings,
            "learned_embeddings": learned_sparse_embeddings,
            "interim_mask_output": interim_mask_output,
            "pred_boxes": pred_boxes,
            "sparse_embeddings": output_sparse_embeddings,
            "dense_embeddings": mask_embeddings
            if learned_dense_embeddings is None
            else learned_dense_embeddings,
        }
